import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun, IncidentReplayStep
from app.replay.writer import ReplayWriter


@pytest.fixture()
def run_id() -> int:
    with Session(get_control_engine()) as s:
        r = AgentRun(incident_id=999003, thread_id=f"t-w-{uuid.uuid4().hex[:8]}",
                     status="created")
        s.add(r)
        s.commit()
        s.refresh(r)
        return r.id


def test_write_started_then_completed_two_rows_same_logical_id(run_id):
    w = ReplayWriter(999003, run_id)
    w.write("EVIDENCE_COLLECTION", "started", logical_step_id="ls-e1",
            state_before={"facts": {}})
    w.complete("EVIDENCE_COLLECTION", "ls-e1",
               state_after={"facts": {"F_INDEX_MISSING": True}}, outcome="succeeded")
    with Session(get_control_engine()) as s:
        rows = s.scalars(select(IncidentReplayStep).where(
            IncidentReplayStep.agent_run_id == run_id)).all()
        assert len(rows) == 2
        phases = sorted(r.phase for r in rows)
        assert phases == ["completed", "started"]
        assert {r.sequence_no for r in rows} == {1, 2}
        assert rows[0].logical_step_id == rows[1].logical_step_id == "ls-e1"


def test_write_failed_phase_rejects_unknown_phase(run_id):
    w = ReplayWriter(999003, run_id)
    with pytest.raises(ValueError):
        w.write("DIAGNOSIS_EVALUATED", "weird_phase", logical_step_id="ls-bad")


def test_approval_idempotent_reuses_logical_id(run_id):
    w = ReplayWriter(999003, run_id)
    lid = w.existing_logical_id("APPROVAL_DECIDED", "approval:42")
    assert lid is None
    w.write("APPROVAL_DECIDED", "started", logical_step_id="ls-app",
            source_refs={"approval_id": 42, "businessKey": "approval:42"})
    lid2 = w.existing_logical_id("APPROVAL_DECIDED", "approval:42")
    assert lid2 == "ls-app"


def test_unknown_step_type_rejected(run_id):
    w = ReplayWriter(999003, run_id)
    with pytest.raises(ValueError):
        w.write("NOT_A_REAL_STEP", "started", logical_step_id="ls-x")
