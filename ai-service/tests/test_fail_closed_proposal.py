"""V2.0-A 安全回归:未知根因 / 未知动作必须 fail closed,绝不回退默认写动作。

升级方案 V2.0 实施内容第 5 条 + 完成标准"未知 root cause、未知 action、缺少
FixDefinition 时写操作为 0"。V1.0 兼容回退(未知根因 → CREATE_INVENTORY_INDEX)已删除。
"""
import uuid

import pytest
from sqlalchemy.orm import Session

from app.agent.nodes import execute_fix, human_approval, propose_fix
from app.agent import fix_registry
from app.db.engine import get_control_engine
from app.db.models import FixExecution
from app.repositories import approval_repo, proposal_repo


def test_build_proposal_unknown_root_cause_raises():
    with pytest.raises(ValueError, match="unknown_root_cause"):
        fix_registry.build_proposal({"incident_id": 1, "run_id": 2,
                                     "root_cause_code": "CACHE_STAMPede_RUMOR"})


def test_build_proposal_missing_root_cause_raises():
    with pytest.raises(ValueError, match="unknown_root_cause"):
        fix_registry.build_proposal({"incident_id": 1, "run_id": 2, "evidence": []})


def test_propose_fix_unknown_root_cause_creates_no_proposal(monkeypatch):
    calls = []
    monkeypatch.setattr("app.agent.nodes.proposal_repo.create_proposal",
                        lambda **kw: calls.append("proposal"))
    monkeypatch.setattr("app.agent.nodes.approval_repo.create_approval",
                        lambda **kw: calls.append("approval"))
    monkeypatch.setattr("app.agent.nodes.event_repo.append_event", lambda *a, **kw: None)
    state = {"incident_id": 1, "run_id": 2, "root_cause_code": "BOGUS_CAUSE",
             "status": "investigating"}
    out = propose_fix(state)
    assert calls == []            # 零提案、零审批
    assert out["status"] == "needs_human"
    assert out["termination_reason"] == "unknown_root_cause"
    assert "fix_proposal" not in out and "approval" not in out


def test_human_approval_skips_fail_closed_path():
    """无提案 / 非 awaiting_approval 状态不得 interrupt(未挂起直接放行)。"""
    state = {"incident_id": 1, "status": "needs_human",
             "termination_reason": "unknown_root_cause"}
    out = human_approval(state)   # 若走到 interrupt 会因无 checkpointer 抛错
    assert out["status"] == "needs_human"


def test_execute_fix_unknown_action_type_performs_zero_writes(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("未知动作不得触达任何写执行器")

    monkeypatch.setattr("app.services.session_terminator.execute", _boom)
    monkeypatch.setattr("app.agent.nodes.fix_service.execute_fix", _boom)
    monkeypatch.setattr("app.agent.nodes.event_repo.append_event", lambda *a, **kw: None)
    state = {"incident_id": 1, "status": "executing",
             "fix_proposal": {"fix_proposal_id": 1, "action_type": "DROP_DATABASE",
                              "parameters": {}, "parameters_hash": "x"},
             "approval": {"approval_id": 1, "status": "approved"}}
    out = execute_fix(state)
    assert out["status"] == "failed"
    assert "unknown_action_type" in (out.get("error") or "")
    assert "fix_execution" not in out


def test_fix_service_unknown_action_rejected_no_execution_row():
    """真库验证:未知 action_type 提案 → UNKNOWN_FIX_ACTION,fix_execution 零新增。"""
    incident_id = int(uuid.uuid4().hex[:8], 16) % 900_000_000 + 100_000_000  # 隔离 id 空间
    proposal = proposal_repo.create_proposal(
        incident_id=incident_id, action_type="SHUTDOWN_SERVER", risk_level="high",
        parameters={"x": 1}, parameters_hash="hash-x", reason="t")
    approval = approval_repo.create_approval(
        incident_id=incident_id, fix_proposal_id=proposal.id,
        action_type="SHUTDOWN_SERVER", parameters_hash="hash-x")
    approval_repo.update_approval(approval.id, status="approved", approver="t")

    with Session(get_control_engine()) as s:
        before = s.query(FixExecution).count()
    from app.services import fix_service
    with pytest.raises(ValueError, match="UNKNOWN_FIX_ACTION"):
        fix_service.execute_fix(incident_id=incident_id, fix_proposal_id=proposal.id,
                                approval_id=approval.id)
    with Session(get_control_engine()) as s:
        assert s.query(FixExecution).count() == before   # 写操作为 0
