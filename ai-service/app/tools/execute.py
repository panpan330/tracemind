import hashlib
import json
import time
import uuid
from typing import Any

from pydantic import ValidationError

from app.repositories.event_repo import append_event
from app.repositories.tool_repo import record_tool_call
from app.tools.registry import TOOL_REGISTRY
from app.tools.schemas import ToolResult

# 离线评测 Fixture:key = f"{tool_name}:{sha256(args_json)[:12]}"
_EVAL_FIXTURE: dict = {}


def set_eval_fixture(fixture: dict | None) -> None:
    """离线评测注入:fixture = {f"{tool_name}:{canonical_args_hash}": {"ok":..., "data":...}}"""
    global _EVAL_FIXTURE
    _EVAL_FIXTURE = fixture or {}


def execute_tool(tool_name: str, incident_id: int | None = None, **kwargs: Any) -> dict:
    """统一工具执行:参数校验、计时、成功/失败封装、审计落库。

    incident_id 由调用方显式传入(路径参数),kwargs 中出现的 incident_id
    一律剔除(防伪造);工具 schema 需要 incident_id 时自动注入。
    """
    # 离线评测 Fixture 命中优先;fixture 非空时不再补真实数据
    args = {k: v for k, v in kwargs.items() if k != "incident_id"}
    key = tool_name + ":" + hashlib.sha256(
        json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    if key in _EVAL_FIXTURE:
        fx = _EVAL_FIXTURE[key]
        # fixture 统一包装为 ToolResult 结构(与真实工具契约一致)
        return ToolResult(
            success=bool(fx.get("ok", True)),
            data=fx.get("data"),
            error_code=fx.get("error_code") or ("" if fx.get("ok") else "FIXTURE_FAILED"),
            error_message=fx.get("error", ""),
        ).model_dump()
    if _EVAL_FIXTURE:
        return ToolResult(success=False, error_code="FIXTURE_NOT_FOUND",
                          error_message="fixture 未命中(离线模式不补真实数据)").model_dump()
    spec = TOOL_REGISTRY.get(tool_name)
    if spec is None:
        return ToolResult(success=False, error_code="UNKNOWN_TOOL",
                          error_message=f"unknown tool: {tool_name}").model_dump()
    kwargs.pop("incident_id", None)
    if incident_id is not None and "incident_id" in spec.input_schema.model_fields:
        kwargs["incident_id"] = incident_id
    try:
        parsed = spec.input_schema(**kwargs)
    except ValidationError as e:
        return ToolResult(success=False, error_code="VALIDATION_ERROR",
                          error_message=str(e)).model_dump()
    start = time.monotonic()
    try:
        data = spec.fn(**parsed.model_dump())
        result = ToolResult(tool_call_id=str(uuid.uuid4())[:8], success=True,
                            duration_ms=int((time.monotonic() - start) * 1000), data=data)
    except ValueError as e:
        result = ToolResult(tool_call_id=str(uuid.uuid4())[:8], success=False,
                            duration_ms=int((time.monotonic() - start) * 1000),
                            error_code=str(e), error_message=str(e))
    except Exception as e:  # noqa: BLE001
        result = ToolResult(tool_call_id=str(uuid.uuid4())[:8], success=False,
                            duration_ms=int((time.monotonic() - start) * 1000),
                            error_code="TOOL_ERROR", error_message=str(e))
    if incident_id is not None:
        record_tool_call(incident_id, tool_name, kwargs, result.model_dump())
        append_event(incident_id, "tool_call",
                     {"tool": tool_name, "result": result.model_dump()})
    return result.model_dump()
