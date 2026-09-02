"""V2.0-A:审批原子 CAS(应用侧 now_utc)+ 审批绑定准确 Run(approval.agent_run_id)。"""
import uuid
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun, Approval, Incident, utcnow
from app.repositories import approval_repo
from app.services.approval_scanner import scan_expired_approvals_once


def _make_incident() -> Incident:
    with Session(get_control_engine()) as s:
        inc = Incident(title="cas-t", description="x", severity="high",
                       service_ref="inventory-service")
        s.add(inc)
        s.commit()
        s.refresh(inc)
        return inc


def _make_run(incident_id: int, status: str = "investigating") -> AgentRun:
    with Session(get_control_engine()) as s:
        r = AgentRun(incident_id=incident_id,
                     thread_id=f"t-cas-{uuid.uuid4().hex[:10]}", status=status)
        s.add(r)
        s.commit()
        s.refresh(r)
        return r


def test_create_approval_binds_agent_run():
    inc = _make_incident()
    a = approval_repo.create_approval(inc.id, 1, "CREATE_INVENTORY_INDEX", "h",
                                      agent_run_id=7)
    assert a.agent_run_id == 7


def test_decide_cas_success_then_already_decided():
    inc = _make_incident()
    a = approval_repo.create_approval(inc.id, 1, "CREATE_INVENTORY_INDEX", "h",
                                      agent_run_id=1)
    out = approval_repo.decide_approval_cas(
        a.id, decision="approved", approver="demo", comment=None, now_utc=utcnow())
    assert out is not None and out.status == "approved"
    # 第二次决定:CAS 失败(pending 条件不满足)
    assert approval_repo.decide_approval_cas(
        a.id, decision="rejected", approver="demo", comment=None, now_utc=utcnow()) is None


def test_decide_cas_expired_returns_none():
    inc = _make_incident()
    with Session(get_control_engine()) as s:
        a = Approval(incident_id=inc.id, fix_proposal_id=1, action_type="X",
                     parameters_hash="h", status="pending",
                     expires_at=utcnow() - timedelta(minutes=1))
        s.add(a)
        s.commit()
        s.refresh(a)
    assert approval_repo.decide_approval_cas(
        a.id, decision="approved", approver="demo", comment=None,
        now_utc=utcnow()) is None
    with Session(get_control_engine()) as s:
        assert s.get(Approval, a.id).status == "pending"  # 未被改写


def test_decide_cas_expiry_boundary_is_exclusive():
    """过期 2 秒的审批必拒(DATETIME 秒级存储,同秒边界不可靠,取明确过期点)。"""
    inc = _make_incident()
    with Session(get_control_engine()) as s:
        a = Approval(incident_id=inc.id, fix_proposal_id=1, action_type="X",
                     parameters_hash="h", status="pending",
                     expires_at=utcnow() - timedelta(seconds=2))
        s.add(a)
        s.commit()
        s.refresh(a)
    assert approval_repo.decide_approval_cas(
        a.id, decision="approved", approver="demo", comment=None,
        now_utc=utcnow()) is None


def test_scanner_resumes_bound_run_not_latest():
    """incident 存在多个 Run 时,过期扫描只恢复 approval 绑定的 Run。"""
    inc = _make_incident()
    run_old = _make_run(inc.id)
    run_new = _make_run(inc.id)   # 更新的 Run(list_runs[0])
    with Session(get_control_engine()) as s:
        a = Approval(incident_id=inc.id, fix_proposal_id=1, action_type="X",
                     parameters_hash="h", status="pending",
                     agent_run_id=run_old.id,
                     expires_at=utcnow() - timedelta(seconds=1))
        s.add(a)
        s.commit()
        s.refresh(a)

    resumed = []

    async def fake_resume(thread_id, resume_value):
        resumed.append(thread_id)

    import asyncio

    import app.services.approval_scanner as scanner
    orig = scanner.resume_investigation
    scanner.resume_investigation = fake_resume
    try:
        count = asyncio.run(scanner.scan_expired_approvals_once())
    finally:
        scanner.resume_investigation = orig
    assert count >= 1
    # 绑定的 Run 必须被恢复;更新的 Run 绝不能被恢复(禁止按 incident 猜最近 Run)
    assert run_old.thread_id in resumed
    assert run_new.thread_id not in resumed
    with Session(get_control_engine()) as s:
        assert s.get(Approval, a.id).status == "expired"
