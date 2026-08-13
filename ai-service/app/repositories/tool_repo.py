from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import ToolCall


def record_tool_call(incident_id: int | None, tool_name: str,
                     input_data: dict, output: dict,
                     agent_run_id: int | None = None,
                     transport: str = "legacy_direct",
                     mcp_invocation_id: str | None = None,
                     mcp_attempt: int | None = None,
                     tool_call_id: str | None = None,
                     purpose: str = "investigation",
                     context_version: str | None = None) -> ToolCall:
    with Session(get_control_engine()) as session:
        call = ToolCall(
            incident_id=incident_id,
            agent_run_id=agent_run_id,
            tool_call_id=tool_call_id,
            purpose=purpose,
            context_version=context_version,
            tool_name=tool_name,
            input=input_data,
            output=output,
            status="success" if output.get("success") else "failed",
            duration_ms=output.get("duration_ms", 0),
            transport=transport,
            mcp_invocation_id=mcp_invocation_id,
            mcp_attempt=mcp_attempt,
        )
        session.add(call)
        session.commit()
        session.refresh(call)
        return call


def list_tool_calls(incident_id: int) -> list[ToolCall]:
    from sqlalchemy import select
    with Session(get_control_engine()) as session:
        return list(session.scalars(
            select(ToolCall).filter(ToolCall.incident_id == incident_id)
            .order_by(ToolCall.id.asc())).all())
