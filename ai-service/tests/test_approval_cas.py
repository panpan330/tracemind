"""V2.0-A:审批原子 CAS(应用侧 now_utc)+ 审批绑定准确 Run(approval.agent_run_id)。"""
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun, Approval, Incident, utcnow
from app.repositories import approval_repo, incident_repo
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


# ---------- V2.0-A closure:绑定损坏一律 fail closed,绝不回退 runs[0] ----------

def _bind_expired_approval(inc_id: int, agent_run_id) -> int:
    with Session(get_control_engine()) as s:
        a = Approval(incident_id=inc_id, fix_proposal_id=1, action_type="X",
                     parameters_hash="h", status="pending",
                     agent_run_id=agent_run_id,
                     expires_at=utcnow() - timedelta(seconds=1))
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


def test_scanner_null_binding_fails_closed_not_latest_run():
    """NULL 绑定:不恢复任何 Run(即使 incident 有多个 Run),Incident 转人工。"""
    inc = _make_incident()
    run_old = _make_run(inc.id)
    run_new = _make_run(inc.id)
    _bind_expired_approval(inc.id, None)

    resumed = []

    async def fake_resume(thread_id, resume_value):
        resumed.append(thread_id)

    import asyncio

    import app.services.approval_scanner as scanner
    orig = scanner.resume_investigation
    scanner.resume_investigation = fake_resume
    try:
        asyncio.run(scanner.scan_expired_approvals_once())
    finally:
        scanner.resume_investigation = orig
    assert resumed == []  # 不猜测最近 Run
    with Session(get_control_engine()) as s:
        row = s.get(Incident, inc.id)
        assert row.status == "needs_human"
        assert row.termination_reason == "approval_run_binding_missing"


def test_scanner_invalid_binding_fails_closed():
    """无效绑定(指向不存在 Run):不恢复、转人工。"""
    import asyncio

    import app.services.approval_scanner as scanner
    inc = _make_incident()
    _make_run(inc.id)
    _bind_expired_approval(inc.id, 999_999_999)

    resumed = []
    orig = scanner.resume_investigation

    async def fake_resume(thread_id, resume_value):
        resumed.append(thread_id)

    scanner.resume_investigation = fake_resume
    try:
        asyncio.run(scanner.scan_expired_approvals_once())
    finally:
        scanner.resume_investigation = orig
    assert resumed == []
    with Session(get_control_engine()) as s:
        row = s.get(Incident, inc.id)
        assert row.termination_reason == "approval_run_binding_invalid"


def test_scanner_mismatched_binding_fails_closed():
    """绑定指向其他 Incident 的 Run:不恢复、转人工(匹配校验)。"""
    import asyncio

    import app.services.approval_scanner as scanner
    inc_a = _make_incident()
    inc_b = _make_incident()
    run_of_b = _make_run(inc_b.id)
    _bind_expired_approval(inc_a.id, run_of_b.id)

    resumed = []
    orig = scanner.resume_investigation

    async def fake_resume(thread_id, resume_value):
        resumed.append(thread_id)

    scanner.resume_investigation = fake_resume
    try:
        asyncio.run(scanner.scan_expired_approvals_once())
    finally:
        scanner.resume_investigation = orig
    assert resumed == []
    with Session(get_control_engine()) as s:
        row = s.get(Incident, inc_a.id)
        assert row.termination_reason == "approval_run_binding_invalid"


def test_decide_api_null_binding_fails_closed(monkeypatch):
    """API 决定 NULL 绑定审批 → 409 + 审批失效 + Incident 转人工,不恢复任何 Run。"""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    calls = {"resume": 0, "invalidated": 0}

    def fake_get_approval(approval_id):
        return SimpleNamespace(id=approval_id, incident_id=1, status="pending",
                               expires_at=datetime.utcnow() + timedelta(minutes=5),
                               agent_run_id=None)

    def fake_cas(approval_id, **kw):
        return SimpleNamespace(id=approval_id, incident_id=1, status="approved")

    def fake_update(approval_id, **kw):
        calls["invalidated"] += 1
        return None

    def fake_incident_status(incident_id, status, **kw):
        calls["incident"] = (incident_id, status)
        return None

    async def fake_resume(thread_id, resume_value):
        calls["resume"] += 1

    monkeypatch.setattr("app.api.approvals.approval_repo.get_approval", fake_get_approval)
    monkeypatch.setattr("app.api.approvals.approval_repo.decide_approval_cas", fake_cas)
    monkeypatch.setattr("app.api.approvals.approval_repo.update_approval", fake_update)
    monkeypatch.setattr("app.api.approvals.incident_repo.update_status", fake_incident_status)
    monkeypatch.setattr("app.api.approvals.resume_investigation", fake_resume)

    resp = client.post("/api/incidents/1/approvals/5/decision",
                       json={"decision": "approved"})
    assert resp.status_code == 409
    assert "approval_run_binding_missing" in resp.json()["detail"]
    assert calls["resume"] == 0 and calls["invalidated"] == 1
    assert calls["incident"] == (1, "needs_human")


def test_decide_api_stale_run_object_binding_fails_closed(monkeypatch):
    """决定期间绑定的 Run 被删除(读取时不存在)→ fail closed,不回退其他 Run。"""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)

    def fake_get_approval(approval_id):
        return SimpleNamespace(id=approval_id, incident_id=1, status="pending",
                               expires_at=datetime.utcnow() + timedelta(minutes=5),
                               agent_run_id=12345)

    def fake_cas(approval_id, **kw):
        return SimpleNamespace(id=approval_id, incident_id=1, status="approved")

    monkeypatch.setattr("app.api.approvals.approval_repo.get_approval", fake_get_approval)
    monkeypatch.setattr("app.api.approvals.approval_repo.decide_approval_cas", fake_cas)
    monkeypatch.setattr("app.api.approvals.run_repo.get_run", lambda run_id: None)
    monkeypatch.setattr("app.api.approvals.approval_repo.update_approval", lambda *a, **kw: None)
    monkeypatch.setattr("app.api.approvals.incident_repo.update_status", lambda *a, **kw: None)

    async def fake_resume(thread_id, resume_value):
        raise AssertionError("绑定无效不得恢复")

    monkeypatch.setattr("app.api.approvals.resume_investigation", fake_resume)
    resp = client.post("/api/incidents/1/approvals/5/decision",
                       json={"decision": "approved"})
    assert resp.status_code == 409
    assert "approval_run_binding_invalid" in resp.json()["detail"]
