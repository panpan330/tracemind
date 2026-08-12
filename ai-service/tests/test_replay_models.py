import uuid

from sqlalchemy import delete, inspect
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import IncidentReplayStep


def test_replay_step_table_created():
    insp = inspect(get_control_engine())
    assert "incident_replay_step" in insp.get_table_names()


def test_replay_step_insert():
    run_id = int(uuid.uuid4().int % 10**7)
    with Session(get_control_engine()) as s:
        s.execute(delete(IncidentReplayStep).where(
            IncidentReplayStep.agent_run_id == run_id))
        s.commit()
        step = IncidentReplayStep(
            incident_id=run_id, agent_run_id=run_id, logical_step_id="ls-1",
            phase="started", step_type="DIAGNOSIS_EVALUATED", sequence_no=1,
            replay_schema_version="1.0", policy_bundle_version="1.0",
        )
        s.add(step)
        s.commit()
        s.refresh(step)
        assert step.id is not None
        assert step.phase == "started"
