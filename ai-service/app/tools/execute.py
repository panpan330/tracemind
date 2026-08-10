import time
import uuid
from typing import Any

from pydantic import ValidationError

from app.repositories.event_repo import append_event
from app.repositories.tool_repo import record_tool_call
from app.tools.registry import TOOL_REGISTRY
from app.tools.schemas import ToolResult


def execute_tool(tool_name: str, incident_id: int | None, **kwargs: Any) -> dict:
    """统一工具执行:参数校验、计时、成功/失败封装、审计落库。"""
    spec = TOOL_REGISTRY.get(tool_name)
    if spec is None:
        return ToolResult(success=False, error_code="UNKNOWN_TOOL",
                          error_message=f"unknown tool: {tool_name}").model_dump()
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
