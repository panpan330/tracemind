import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun


def create_run(incident_id: int, baseline: dict | None = None) -> AgentRun:
    with Session(get_control_engine()) as session:
        run = AgentRun(incident_id=incident_id, thread_id=f"run-{uuid.uuid4()}",
                       status="created", incident_digest_baseline=baseline)
        session.add(run)
        session.commit()
        session.refresh(run)
        return run


def get_run(run_id: int) -> AgentRun | None:
    with Session(get_control_engine()) as session:
        return session.get(AgentRun, run_id)


def list_runs(incident_id: int) -> list[AgentRun]:
    with Session(get_control_engine()) as session:
        return list(session.scalars(
            select(AgentRun)
            .filter(AgentRun.incident_id == incident_id)
            .order_by(AgentRun.id.desc())).all())
