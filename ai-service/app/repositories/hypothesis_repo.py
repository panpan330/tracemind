from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import Hypothesis


def upsert_hypothesis(incident_id: int, description: str, status: str) -> Hypothesis:
    """按 (incident_id, description) 幂等写入/更新假设。"""
    with Session(get_control_engine()) as session:
        existing = session.scalars(select(Hypothesis).filter(
            Hypothesis.incident_id == incident_id,
            Hypothesis.description == description).limit(1)).first()
        if existing is not None:
            existing.status = status
            session.commit()
            session.refresh(existing)
            return existing
        h = Hypothesis(incident_id=incident_id, description=description, status=status)
        session.add(h)
        session.commit()
        session.refresh(h)
        return h


def list_by_incident(incident_id: int) -> list[Hypothesis]:
    with Session(get_control_engine()) as session:
        return list(session.scalars(select(Hypothesis).filter(
            Hypothesis.incident_id == incident_id).order_by(Hypothesis.id.asc())).all())
