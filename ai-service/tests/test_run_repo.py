import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun
from app.repositories import run_repo


@pytest.fixture()
def run_id() -> int:
    with Session(get_control_engine()) as s:
        r = AgentRun(incident_id=999001, thread_id=f"t-replay-{uuid.uuid4().hex[:8]}",
                     status="created")
        s.add(r)
        s.commit()
        s.refresh(r)
        return r.id


def test_allocate_sequence_atomic_and_monotonic(run_id):
    a = run_repo.allocate_replay_sequence(run_id)
    b = run_repo.allocate_replay_sequence(run_id)
    assert b == a + 1  # 单调递增


def test_freeze_versions(run_id):
    run_repo.freeze_run_versions(run_id, "1.0")
    with Session(get_control_engine()) as s:
        r = s.get(AgentRun, run_id)
        assert r.expected_policy_bundle_version == "1.0"


def test_allocate_uses_passed_session(run_id):
    """序号分配与插入可在同一事务:传入 Session 时不自行 open。"""
    with Session(get_control_engine()) as s:
        seq = run_repo.allocate_replay_sequence(run_id, s)
        s.commit()
    assert seq >= 1
