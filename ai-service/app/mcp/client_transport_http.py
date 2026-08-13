"""Streamable HTTP Client Adapter:共享连接池 + 静态 Auth + 逐请求 Headers + 幂等重试。"""
import asyncio
import json
import uuid
from typing import Any, Optional

from app.config.mcp import McpClientSettings
from app.mcp.client_errors import (ClientError, MCP_DISCONNECTED, MCP_REQUEST_TIMEOUT)
from app.tools_core.context import ClientInvocationContext


class McpHttpTransport:
    def __init__(self, settings: McpClientSettings):
        self._settings = settings
        self._session = None
        self._client_attempts: dict[str, str] = {}   # tool_call_id → client_attempt_id(重传复用)
        self.protocol_version: Optional[str] = None

    # ---- 逐请求 Headers(禁止改共享 header dict / 全局当前 incident)----
    def _build_headers(self, ctx: ClientInvocationContext) -> dict:
        return {
            "Authorization": f"Bearer {self._settings.mcp_http_bearer_token}",
            "X-TraceMind-Incident-Id": str(ctx.incident_id),
            "X-TraceMind-Agent-Run-Id": str(ctx.agent_run_id),
            "X-TraceMind-Tool-Call-Id": ctx.tool_call_id,
            "X-TraceMind-Purpose": ctx.purpose,
            "X-TraceMind-Context-Version": "1",
        }

    async def connect(self) -> None:
        from mcp.client.streamable_http import streamablehttp_client
        try:
            headers = {"Authorization": f"Bearer {self._settings.mcp_http_bearer_token}"}
            self._ctx = streamablehttp_client(self._settings.mcp_http_url,
                                              headers=headers,
                                              timeout=self._settings.mcp_http_request_timeout_seconds)
            # 1.29 返回 (read, write, GetSessionIdCallback) 三元素
            self._read, self._write, self._get_session_id = await self._ctx.__aenter__()
            from mcp import ClientSession
            self._session = await ClientSession(self._read, self._write).__aenter__()
            init = await self._session.initialize()
            self.protocol_version = init.protocolVersion
        except Exception as e:  # noqa: BLE001
            raise ClientError("MCP_CONNECT_FAILED", str(e), retryable=True) from e

    async def list_tools(self) -> list:
        tools = (await self._session.list_tools()).tools
        return [{"name": t.name, "inputSchema": t.inputSchema} for t in tools]

    async def _call_with_retry(self, name: str, params: dict,
                               ctx: ClientInvocationContext) -> dict:
        attempt_id = self._client_attempts.setdefault(ctx.tool_call_id,
                                                      f"ca-{uuid.uuid4().hex[:12]}")
        last_error: Optional[ClientError] = None
        max_retries = self._settings.mcp_http_max_retries  # 含首次,默认 3
        for attempt in range(1, max_retries + 1):
            try:
                result = await self._session.call_tool(name, params)
                return self._parse(result, ctx, attempt, attempt_id)
            except ClientError as e:
                last_error = e
                if not e.retryable:
                    raise
            except Exception as e:  # noqa: BLE001
                last_error = ClientError(MCP_DISCONNECTED, str(e), retryable=True)
            if attempt < max_retries:
                await asyncio.sleep(0.1 * (2 ** attempt))   # 指数退避(简化)
        raise last_error if last_error else ClientError(MCP_DISCONNECTED, "max retries")

    @staticmethod
    def _parse(result, ctx: ClientInvocationContext, attempt: int, attempt_id: str) -> dict:
        text = "".join(getattr(c, "text", "") or "" for c in result.content or []
                       if getattr(c, "type", "") == "text")
        out = json.loads(text)
        out["mcp_invocation_id"] = f"{ctx.tool_call_id}:{attempt_id}:{attempt}"
        return out

    async def call_tool(self, name: str, params: dict, ctx: ClientInvocationContext) -> dict:
        if self._session is None:
            raise ClientError(MCP_DISCONNECTED, "未连接", retryable=True)
        # context 经受控参数注入(LLM 可见 schema 已隐藏这些字段;FastMCP 层不支持逐请求 header)
        args = {**params, "incident_id": ctx.incident_id, "agent_run_id": ctx.agent_run_id}
        return await self._call_with_retry(name, args, ctx)

    async def close(self) -> None:
        if self._session:
            await self._session.__aexit__(None, None, None)
        self._session = None
