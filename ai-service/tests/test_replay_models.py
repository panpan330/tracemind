from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import IncidentReplayStep


def test_replay_step_table_created():
    insp = inspect(get_control_engine())
    assert "incident_replay_step" in insp.get_table_names()


def test_replay_step_insert():
    with Session(get_control_engine()) as s:
        step = IncidentReplayStep(
            incident_id=999002, agent_run_id=999002, logical_step_id="ls-1",
            phase="started", step_type="DIAGNOSIS_EVALUATED", sequence_no=1,
            replay_schema_version="1.0", policy_bundle_version="1.0",
        )
        s.add(step)
        s.commit()
        s.refresh(step)
        assert step.id is not None
        assert step.phase == "started"
