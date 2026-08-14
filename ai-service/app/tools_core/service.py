"""ToolExecutionService:传输无关的统一工具执行(参数校验/上下文校验/执行/审计)。"""
import hashlib
import json
import time
import uuid
from typing import Any, Optional

from pydantic import ValidationError

from app.tools_core.context import ClientInvocationContext, AuthenticatedPrincipal
from app.tools_core.errors import ToolBusinessError
from app.tools_core.ports import ToolAuditPort
from app.tools_core.registry import TOOL_REGISTRY
from app.tools_core.schemas import ToolResult

# reserved context 字段(出现即拒绝,不静默剔除)
_RESERVED_CONTEXT_FIELDS = {"incident_id", "agent_run_id", "tool_call_id", "purpose",
                            "client_id", "traceparent", "tracestate"}


class ToolExecutionService:
    def __init__(self, ports: Optional[dict] = None, runtime: str = "real",
                 fixture: Optional[dict] = None,
                 audit: Optional[ToolAuditPort] = None) -> None:
        from app.tools_core.handlers import build_handlers
        self._handlers = build_handlers(ports or {})
        # 仅当提供了基础设施端口时走 handler 路径;legacy 薄封装(ports 空)回退 registry fn
        self._use_handlers = bool(ports)
        self.runtime = runtime
        self.audit = audit
        self._fixture: dict = {}
        if fixture:
            self.set_fixture(fixture)

    def set_fixture(self, fixture: Optional[dict]) -> None:
        if self.runtime != "fixture":
            raise ToolBusinessError("FIXTURE_FORBIDDEN",
                                    "fixture 仅允许 fixture runtime", retryable=False)
        self._fixture = fixture or {}

    # ---- 内部 ----
    def _fixture_key(self, tool_name: str, args: dict) -> str:
        h = hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
        return f"{tool_name}:{h}"

    def _run_handler(self, name: str, parsed) -> dict:
        """handler 模式:ports 非空时经 handler(新架构);剔除 context 字段(handler 为纯业务)。
        ports 为空(legacy 薄封装)时直接走 TOOL_REGISTRY fn(与 V1.6 行为一致)。"""
        if self._use_handlers:
            fn = self._handlers.get(name)
            if fn is not None:
                args = {k: v for k, v in parsed.model_dump().items()
                        if k not in _RESERVED_CONTEXT_FIELDS}
                return fn(**args)
        spec = TOOL_REGISTRY.get(name)
        if spec is None:
            raise ToolBusinessError("UNKNOWN_TOOL", f"unknown tool: {name}", retryable=False)
        return spec.fn(**parsed.model_dump())

    def execute(self, name: str, params: dict,
                ctx: ClientInvocationContext,
                principal: Optional[AuthenticatedPrincipal] = None,
                transport: str = "mcp_stdio",
                mcp_invocation_id: Optional[str] = None,
                mcp_attempt: Optional[int] = None,
                audit_side: str = "ai",
                mcp_request_id: Optional[str] = None) -> dict:
        # 1) reserved context 字段拒绝(模型可传的只有业务参数)
        overlap = _RESERVED_CONTEXT_FIELDS & set(params)
        if overlap:
            raise ToolBusinessError(
                "MCP_CONTEXT_SPOOFING_REJECTED",
                f"reserved context field(s): {sorted(overlap)}", retryable=False)
        # 2) fixture 命中优先(仅 fixture runtime 有 _fixture)
        if self._fixture:
            key = self._fixture_key(name, {k: v for k, v in params.items() if v is not None})
            if key in self._fixture:
                fx = self._fixture[key]
                return ToolResult(success=bool(fx.get("ok", True)), data=fx.get("data"),
                                  error_code=fx.get("error_code") or ("" if fx.get("ok") else "FIXTURE_FAILED"),
                                  error_message=fx.get("error", "")).model_dump()
            return ToolResult(success=False, error_code="FIXTURE_NOT_FOUND",
                              error_message="fixture 未命中(离线模式不补真实数据)").model_dump()
        # 3) registry 查工具
        spec = TOOL_REGISTRY.get(name)
        if spec is None:
            return ToolResult(success=False, error_code="UNKNOWN_TOOL",
                              error_message=f"unknown tool: {name}").model_dump()
        # 4) strict schema 校验(extra=forbid 在 schema 上保证)
        try:
            parsed = spec.input_schema(**params)
        except ValidationError as e:
            return ToolResult(success=False, error_code="VALIDATION_ERROR",
                              error_message=str(e)).model_dump()
        # 5) 执行
        start = time.monotonic()
        try:
            data = self._run_handler(name, parsed)
            result = ToolResult(tool_call_id=str(uuid.uuid4())[:8], success=True,
                                duration_ms=int((time.monotonic() - start) * 1000), data=data)
        except ToolBusinessError as e:
            result = ToolResult(tool_call_id=str(uuid.uuid4())[:8], success=False,
                                duration_ms=int((time.monotonic() - start) * 1000),
                                error_code=e.code, error_message=e.message,
                                data={"retryable": e.retryable})
        except Exception as e:  # noqa: BLE001
            result = ToolResult(tool_call_id=str(uuid.uuid4())[:8], success=False,
                                duration_ms=int((time.monotonic() - start) * 1000),
                                error_code="TOOL_ERROR", error_message=str(e))
        # 6) 审计唯一所有者:AI 侧写 tool_call;MCP Server 侧经 audit 端口写 tool_call_attempt(两段式)
        if ctx.incident_id:
            if audit_side == "mcp" and self.audit is not None:
                try:
                    pk = self.audit.write_attempt_started(ctx, mcp_attempt or 1,
                                                           mcp_request_id or mcp_invocation_id or "")
                    self.audit.write_attempt_finished(
                        pk, "completed" if result.get("success") else "failed",
                        result=result, error_code=result.get("error_code"),
                        retryable=bool(result.get("data", {}).get("retryable")),
                        latency_ms=result.get("duration_ms", 0))
                except Exception:  # noqa: BLE001  终态审计失败不改变工具结果(已有 started)
                    pass
            else:
                from app.repositories.tool_repo import record_tool_call
                record_tool_call(ctx.incident_id, name, params, result.model_dump(),
                                 agent_run_id=ctx.agent_run_id, transport=transport,
                                 mcp_invocation_id=mcp_invocation_id, mcp_attempt=mcp_attempt,
                                 tool_call_id=ctx.tool_call_id, purpose=ctx.purpose)
        return result.model_dump()
