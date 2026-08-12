import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun, IncidentReplayStep
from app.replay.writer import ReplayWriter
from app.api import approvals as approvals_api


@pytest.fixture()
def run_id() -> int:
    with Session(get_control_engine()) as s:
        r = AgentRun(incident_id=999006, thread_id=f"t-ap-{uuid.uuid4().hex[:8]}",
                     status="created")
        s.add(r)
        s.commit()
        s.refresh(r)
        return r.id


def test_approval_decision_writes_step_once(run_id):
    """审批决定写 APPROVAL_DECIDED;重复提交(客户端重试)幂等,不生成重复步骤。"""
    w = ReplayWriter(999006, run_id)
    approvals_api.replay_writer = w
    try:
        approvals_api._record_approval_decided(999006, run_id, 42, "approved")
        approvals_api._record_approval_decided(999006, run_id, 42, "approved")
    finally:
        approvals_api.replay_writer = None
    with Session(get_control_engine()) as s:
        rows = s.scalars(select(IncidentReplayStep).where(
            IncidentReplayStep.agent_run_id == run_id,
            IncidentReplayStep.step_type == "APPROVAL_DECIDED")).all()
        # 一次逻辑步骤(started + completed),重复提交不新增
        assert len(rows) == 2
        assert {r.phase for r in rows} == {"started", "completed"}
        assert {r.logical_step_id for r in rows} == {"ls-app-42"}


def test_human_approval_writes_request_step(run_id, monkeypatch):
    """human_approval 写 APPROVAL_REQUESTED(进入审批挂起)。"""
    from app.agent import nodes
    monkeypatch.setattr("app.agent.nodes.interrupt", lambda payload: {"decision": "approved"})
    state = {"incident_id": 999006, "run_id": run_id, "status": "awaiting_approval",
             "fix_proposal": {"fix_proposal_id": 1, "action_type": "CREATE_INVENTORY_INDEX",
                              "risk_level": "medium"},
             "approval": {"approval_id": 7}}
    nodes.human_approval(state)
    with Session(get_control_engine()) as s:
        rows = s.scalars(select(IncidentReplayStep).where(
            IncidentReplayStep.agent_run_id == run_id,
            IncidentReplayStep.step_type == "APPROVAL_REQUESTED")).all()
        assert len(rows) == 1
        assert rows[0].phase == "completed"
        assert rows[0].source_references_json.get("approval_id") == 7
