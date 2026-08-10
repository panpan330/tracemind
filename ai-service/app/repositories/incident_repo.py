from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import Incident


def create_incident(title: str, description: str | None, severity: str,
                    service_ref: str, observed_at: datetime | None = None) -> Incident:
    with Session(get_control_engine()) as session:
        inc = Incident(title=title, description=description, severity=severity,
                       service_ref=service_ref, observed_at=observed_at, status="created")
        session.add(inc)
        session.commit()
        session.refresh(inc)
        return inc


def save_incident_baseline(incident_id: int, baseline: dict) -> None:
    with Session(get_control_engine()) as session:
        inc = session.get(Incident, incident_id)
        inc.healthy_metrics_baseline = baseline
        session.commit()


def get_incident(incident_id: int) -> Incident | None:
    with Session(get_control_engine()) as session:
        return session.get(Incident, incident_id)


def list_incidents() -> list[Incident]:
    with Session(get_control_engine()) as session:
        return list(session.scalars(select(Incident).order_by(Incident.id.desc())).all())


def update_status(incident_id: int, status: str) -> None:
    with Session(get_control_engine()) as session:
        inc = session.get(Incident, incident_id)
        if inc is None:
            return
        inc.status = status
        session.commit()
