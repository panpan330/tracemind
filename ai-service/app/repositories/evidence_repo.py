from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import Evidence


def upsert_evidence(incident_id: int, key: str, source: str,
                    content: dict | None, passed: bool) -> Evidence:
    """按 (incident_id, source) 幂等写入/更新证据;key(E1~E5)随 content 存储。"""
    with Session(get_control_engine()) as session:
        existing = session.scalars(select(Evidence).filter(
            Evidence.incident_id == incident_id,
            Evidence.source == source).limit(1)).first()
        payload = {"key": key, "passed": passed, "data": content}
        if existing is not None:
            existing.content = payload
            session.commit()
            session.refresh(existing)
            return existing
        ev = Evidence(incident_id=incident_id, source=source, content=payload)
        session.add(ev)
        session.commit()
        session.refresh(ev)
        return ev


def list_by_incident(incident_id: int) -> list[Evidence]:
    with Session(get_control_engine()) as session:
        return list(session.scalars(select(Evidence).filter(
            Evidence.incident_id == incident_id).order_by(Evidence.id.asc())).all())
