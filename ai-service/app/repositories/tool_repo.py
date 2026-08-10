from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import ToolCall


def record_tool_call(incident_id: int | None, tool_name: str,
                     input_data: dict, output: dict) -> ToolCall:
    with Session(get_control_engine()) as session:
        call = ToolCall(
            incident_id=incident_id,
            tool_name=tool_name,
            input=input_data,
            output=output,
            status="success" if output.get("success") else "failed",
            duration_ms=output.get("duration_ms", 0),
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
