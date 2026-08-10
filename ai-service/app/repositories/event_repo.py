from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import IncidentEvent


def _next_sequence(session: Session, incident_id: int) -> int:
    current = session.execute(
        select(func.coalesce(func.max(IncidentEvent.sequence), 0))
        .filter(IncidentEvent.incident_id == incident_id)).scalar_one()
    return int(current) + 1


def append_event(incident_id: int, event_type: str, payload: dict | None = None) -> IncidentEvent:
    with Session(get_control_engine()) as session:
        event = IncidentEvent(incident_id=incident_id,
                              sequence=_next_sequence(session, incident_id),
                              event_type=event_type, payload=payload)
        session.add(event)
        session.commit()
        session.refresh(event)
        return event


def list_events(incident_id: int, after_sequence: int = 0) -> list[IncidentEvent]:
    with Session(get_control_engine()) as session:
        return list(session.scalars(
            select(IncidentEvent)
            .filter(IncidentEvent.incident_id == incident_id,
                    IncidentEvent.sequence > after_sequence)
            .order_by(IncidentEvent.sequence.asc())).all())
