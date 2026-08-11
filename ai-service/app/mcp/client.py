"""McpClientManager:后台线程 + 唯一 event loop + stdio ClientSession,同步桥接。
同步 call_tool 经 run_coroutine_threadsafe 提交到专用 loop,单会话串行(Semaphore(1))。"""
import asyncio
import json
import logging
import os
import sys
import threading
import uuid
from typing import Any

from app.config import settings
from app.mcp.contract import MCP_TOOL_CONTRACT_VERSION, verify_contract

logger = logging.getLogger(__name__)

MCP_START_FAILED = "MCP_START_FAILED"
MCP_SCHEMA_MISMATCH = "MCP_SCHEMA_MISMATCH"
MCP_TIMEOUT = "MCP_TIMEOUT"
MCP_DISCONNECTED = "MCP_DISCONNECTED"
MCP_PROTOCOL_ERROR = "MCP_PROTOCOL_ERROR"
MCP_TOOL_ERROR = "MCP_TOOL_ERROR"
MCP_RESULT_INVALID = "MCP_RESULT_INVALID"


class MCPError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


# 子进程环境白名单:不传 fix_executor/LLM/Embedding/Qdrant Write 凭据
_ENV_ALLOWLIST = frozenset({
    "TRACEMIND_CONTROL_DB_URL", "TRACEMIND_READONLY_DB_URL",
    "TRACEMIND_ORDER_SERVICE_URL", "TRACEMIND_INVENTORY_SERVICE_URL",
    "TRACEMIND_EVAL_MODE", "TRACEMIND_EVAL_FIXTURE_DIR", "TRACEMIND_MCP_*",
})


def _spawn_env() -> dict:
    env = {"PYTHONUNBUFFERED": "1"}
    for k in _ENV_ALLOWLIST:
        if k.endswith("*"):
            prefix = k[:-1]
            env.update({ek: v for ek, v in os.environ.items() if ek.startswith(prefix)})
        elif k in os.environ:
            env[k] = os.environ[k]
    return env


class McpClientManager:
    def __init__(self, fixture_file: str | None = None) -> None:
        self.fixture_file = fixture_file
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session = None
        self._stdio_ctx = None
        self._sem = threading.Semaphore(1)
        self.is_ready = False
        self._timeout = settings.mcp_timeout_seconds
        self.max_restart = settings.mcp_max_restart
        self._invocation_id = 0

    async def _run_loop(self, ready: asyncio.Event) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            await self._start_session(ready)
            await self._loop.run_forever()
        finally:
            await self._close_session()

    async def _start_session(self, ready: asyncio.Event) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        attempts = 0
        while True:
            attempts += 1
            try:
                cmd = [sys.executable, "-m", "app.mcp.server"]
                if self.fixture_file:
                    cmd += ["--fixture-file", self.fixture_file]
                params = StdioServerParameters(command=cmd[0], args=cmd[1:],
                                               env=_spawn_env())
                self._stdio_ctx = stdio_client(params)
                self._read, self._write = await self._stdio_ctx.__aenter__()
                self._session = await ClientSession(self._read, self._write).__aenter__()
                await self._session.initialize()
                server_info = self._session.get_server_info() or {}
                tools = (await self._session.list_tools()).tools
                verify_contract(server_info, [{"name": t.name, "inputSchema": t.inputSchema}
                                              for t in tools])
                self.is_ready = True
                settings.mcp_ready = True
                ready.set()
                logger.info("MCP Server 就绪,contract %s", MCP_TOOL_CONTRACT_VERSION)
                return
            except Exception as exc:  # noqa: BLE001 启动/初始化/契约失败按策略重试
                logger.warning("MCP Server 启动失败(第 %d/%d 次): %s", attempts,
                               self.max_restart + 1, exc)
                await self._close_session()
                if attempts > self.max_restart:
                    settings.mcp_ready = False
                    raise MCPError(MCP_START_FAILED, str(exc)) from exc
                await asyncio.sleep(0.5)

    async def _close_session(self) -> None:
        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._session = None
        if self._stdio_ctx:
            try:
                await self._stdio_ctx.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._stdio_ctx = None
        self.is_ready = False
        settings.mcp_ready = False

    async def start(self) -> None:
        ready = asyncio.Event()
        self._thread = threading.Thread(target=self._run_loop, args=(ready,), daemon=True)
        self._thread.start()
        await asyncio.wait_for(ready.wait(), timeout=30)

    async def stop(self) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None

    def call_tool(self, name: str, incident_id: int, agent_run_id: int,
                  **business: Any) -> dict:
        """同步工具调用:注入上下文 + 桥接到后台 loop,单会话串行。"""
        self._invocation_id += 1
        mcp_invocation_id = f"mcp-{self._invocation_id}-{uuid.uuid4().hex[:8]}"
        args: dict = {"incident_id": incident_id, "agent_run_id": agent_run_id, **business}
        with self._sem:
            if self._session is None or self._loop is None:
                raise MCPError(MCP_DISCONNECTED, "MCP 会话未就绪")
            future = asyncio.run_coroutine_threadsafe(
                self._call_async(name, args), self._loop)
            try:
                result = future.result(timeout=self._timeout)
            except asyncio.TimeoutError as exc:
                future.cancel()   # 取消挂起的协程,避免 loop 关闭时 Task 泄漏
                raise MCPError(MCP_TIMEOUT, str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                raise MCPError(MCP_TOOL_ERROR, str(exc)) from exc
        return self._parse_result(result, mcp_invocation_id)

    async def _call_async(self, name: str, args: dict) -> Any:
        return await self._session.call_tool(name, args)

    @staticmethod
    def _parse_result(result: Any, mcp_invocation_id: str) -> dict:
        if result is None:
            raise MCPError(MCP_RESULT_INVALID, "空响应")
        text = ""
        for c in result.content or []:
            if getattr(c, "type", "") == "text":
                text += getattr(c, "text", "") or ""
        try:
            out = json.loads(text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise MCPError(MCP_RESULT_INVALID, str(exc)) from exc
        if not isinstance(out, dict) or "success" not in out:
            raise MCPError(MCP_RESULT_INVALID, "ToolResult 校验失败")
        out["mcp_invocation_id"] = mcp_invocation_id
        return out


_client: McpClientManager | None = None


def get_mcp_client() -> McpClientManager:
    global _client
    if _client is None or not _client.is_ready:
        raise MCPError(MCP_START_FAILED, "MCP Client 未初始化(业务调用期间不悄悄启动)")
    return _client
