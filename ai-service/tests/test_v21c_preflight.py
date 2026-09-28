"""V2.1-C 提交②:Preflight —— 两条写路径执行前的共享复核。

覆盖(含用户验收钉住项):
- 索引已存在 / 重复执行保持原有**安全 no_op 幂等**语义(Preflight 不得把 no_op 变成拒绝);
- 已自愈(当前 HTTP P95 已恢复)→ 拒绝且零写操作 + needs_human + 原因码;
- **新 FIRING 仅触发重新取证**:不因 last_seen_at 晚于 Proposal 而拒绝;
- 建索引与 KILL 两条写路径都接入同一 Preflight。
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import Approval, FixProposal, Incident
from app.repositories import incident_repo
from app.services import fix_service, preflight, recovery_signal, session_terminator


def _incident(*, alert_status="FIRING") -> int:
    with Session(get_control_engine()) as s:
        inc = Incident(title=f"pf-{uuid.uuid4().hex[:6]}", severity="high",
                       service_ref="order-service",
                       affected_operation_ref="ORDER_CREATE",
                       source="alertmanager", alert_name="OrderOperationP95High",
                       environment="demo", alert_status=alert_status,
                       lifecycle_status="OPEN")
        s.add(inc)
        s.commit()
        s.refresh(inc)
        return inc.id


def _proposal(incident_id: int, action_type="CREATE_INVENTORY_INDEX") -> int:
    with Session(get_control_engine()) as s:
        p = FixProposal(incident_id=incident_id, action_type=action_type,
                        fix_definition_id=1,
                        parameters_json={"processlist_id": 4242,
                                         "blocking_transaction_id": "tx-1"},
                        parameters_hash=f"h-{uuid.uuid4().hex[:10]}",
                        status="proposed")
        s.add(p)
        s.commit()
        s.refresh(p)
        return p.id


def _approval(incident_id: int, proposal_id: int) -> int:
    with Session(get_control_engine()) as s:
        a = Approval(incident_id=incident_id, fix_proposal_id=proposal_id,
                     action_type="CREATE_INVENTORY_INDEX", parameters_hash="h",
                     status="approved", agent_run_id=1,
                     expires_at=datetime.utcnow() + timedelta(minutes=10))
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


@pytest.fixture
def signal_recovered(monkeypatch):
    monkeypatch.setattr(recovery_signal, "measure_current_p95",
                        lambda *a, **kw: recovery_signal.RecoverySignal(
                            status=recovery_signal.STATUS_RECOVERED,
                            source=recovery_signal.SOURCE_SLO, threshold_ms=100.0,
                            p95_ms=15.0, sample_count=80, window_seconds=60))


@pytest.fixture
def signal_degraded(monkeypatch):
    monkeypatch.setattr(recovery_signal, "measure_current_p95",
                        lambda *a, **kw: recovery_signal.RecoverySignal(
                            status=recovery_signal.STATUS_NOT_RECOVERED,
                            source=recovery_signal.SOURCE_SLO, threshold_ms=100.0,
                            p95_ms=400.0, sample_count=80, window_seconds=60))


# ---------- 索引路径 ----------

def test_index_already_present_keeps_no_op(monkeypatch):
    """索引已存在 → 既有 no_op 幂等语义不变(Preflight 不参与、不拒绝、零 DDL)。"""
    inc_id = _incident()
    prop_id = _proposal(inc_id)
    appr_id = _approval(inc_id, prop_id)
    monkeypatch.setattr(fix_service, "_index_present", lambda: True)
    called = {"preflight": 0}

    def _preflight_should_not_run(*a, **kw):
        called["preflight"] += 1
        return preflight.PreflightResult(ok=False, reason="should_not_run", checks={})

    monkeypatch.setattr(preflight, "preflight_for_index", _preflight_should_not_run)
    out = fix_service.execute_fix(inc_id, prop_id, appr_id)
    assert out["status"] == "no_op"
    assert called["preflight"] == 0                 # 已 no_op,无需复核


def test_duplicate_execution_keeps_no_op(monkeypatch):
    """同幂等键已成功执行 → no_op already_executed(不变)。"""
    inc_id = _incident()
    prop_id = _proposal(inc_id)
    appr_id = _approval(inc_id, prop_id)
    monkeypatch.setattr(fix_service, "_index_present", lambda: False)
    with Session(get_control_engine()) as s:
        p = s.get(FixProposal, prop_id)
        from app.db.models import FixExecution
        s.add(FixExecution(incident_id=inc_id, fix_proposal_id=prop_id,
                           approval_id=appr_id,
                           idempotency_key=f"{inc_id}:{prop_id}:{p.parameters_hash}",
                           status="succeeded", result={}))
        s.commit()
    out = fix_service.execute_fix(inc_id, prop_id, appr_id)
    assert out["status"] == "no_op" and out["detail"] == "already_executed"


def test_preflight_rejects_when_self_healed(signal_recovered, monkeypatch):
    """已自愈 → 拒绝执行,零写操作,Incident 转 needs_human + 原因码。"""
    inc_id = _incident(alert_status="RESOLVED")
    prop_id = _proposal(inc_id)
    appr_id = _approval(inc_id, prop_id)
    monkeypatch.setattr(fix_service, "_index_present", lambda: False)
    monkeypatch.setattr(fix_service, "_index_plan_ok", lambda: True)
    with pytest.raises(ValueError) as ei:
        fix_service.execute_fix(inc_id, prop_id, appr_id)
    assert str(ei.value) == "PREFLIGHT_ALREADY_RECOVERED"
    with Session(get_control_engine()) as s:
        n = s.execute(text("SELECT COUNT(*) FROM fix_execution WHERE incident_id=:i"),
                      {"i": inc_id}).scalar()
        inc = s.execute(text("SELECT status, termination_reason FROM incident "
                             "WHERE id=:i"), {"i": inc_id}).fetchone()
    assert n == 0                                     # 零写操作
    assert inc.status == "needs_human"
    assert inc.termination_reason == "PREFLIGHT_ALREADY_RECOVERED"


def test_preflight_passes_on_new_firing_still_degraded(signal_degraded, monkeypatch):
    """新 FIRING(last_seen_at 晚于 Proposal)且指标仍异常 → 复核通过并执行(零拒绝)。"""
    inc_id = _incident(alert_status="FIRING")
    prop_id = _proposal(inc_id)
    appr_id = _approval(inc_id, prop_id)
    monkeypatch.setattr(fix_service, "_index_present", lambda: False)
    monkeypatch.setattr(fix_service, "_index_plan_ok", lambda: True)
    executed = {"ddl": 0}

    def _fake_ddl(ddl):
        executed["ddl"] += 1

    monkeypatch.setattr(fix_service, "_execute_ddl", _fake_ddl)
    with Session(get_control_engine()) as s:           # 更晚的 last_seen_at(时间戳判据已删)
        s.execute(text("UPDATE incident SET last_seen_at=:t WHERE id=:i"),
                  {"t": datetime.utcnow(), "i": inc_id})
        s.commit()
    out = fix_service.execute_fix(inc_id, prop_id, appr_id)
    assert out["status"] == "succeeded"
    assert executed["ddl"] == 1


def test_preflight_records_audit_checks(signal_degraded, monkeypatch):
    """复核结果写入审计字段(告警状态/计划/阈值来源),供坐席核对。"""
    inc_id = _incident(alert_status="FIRING")
    monkeypatch.setattr(fix_service, "_index_present", lambda: False)
    monkeypatch.setattr(fix_service, "_index_plan_ok", lambda: True)
    res = preflight.preflight_for_index(inc_id, {"p95_ms": 50})
    assert res.ok is True
    assert res.checks["alert_status"] == "FIRING"
    assert res.checks["lifecycle_status"] == "OPEN"
    assert res.checks["threshold_source"] in ("baseline", "SLO")
    assert res.checks["p95_ms"] == 400.0


# ---------- KILL 路径 ----------

def test_kill_path_preflight_rejects_when_self_healed(signal_recovered, monkeypatch):
    """KILL 路径同样先过 Preflight:已自愈 → 拒绝,不执行 KILL。"""
    killed = {"n": 0}

    class FakeTerminator:
        def query_blocking(self, pid):
            return {"transaction_id": "tx-1", "holds_lock": True,
                    "account": "app_business", "is_system": False}

        def execute_kill(self, pid):
            killed["n"] += 1

    proposal = {"parameters": {"processlist_id": 4242,
                               "blocking_transaction_id": "tx-1"},
                "parameters_hash": "h"}
    approval = {"status": "approved", "expires_at": None}
    out = session_terminator.execute(proposal, approval, engine=FakeTerminator(),
                                    incident_id=_incident(),
                                    baseline={"p95_ms": 50})
    assert out["execution_result"] == "rejected_preflight_self_healed"
    assert out["kill_attempted"] is False
    assert killed["n"] == 0


def test_kill_path_preflight_passes_when_degraded(signal_degraded, monkeypatch):
    class FakeTerminator:
        def query_blocking(self, pid):
            return {"transaction_id": "tx-1", "holds_lock": True,
                    "account": "app_business", "is_system": False}

        def execute_kill(self, pid):
            return None

    proposal = {"parameters": {"processlist_id": 4242,
                               "blocking_transaction_id": "tx-1"},
                "parameters_hash": "h2"}
    approval = {"status": "approved", "expires_at": None}
    out = session_terminator.execute(proposal, approval, engine=FakeTerminator(),
                                    incident_id=_incident(),
                                    baseline={"p95_ms": 50})
    assert out["execution_result"] == "executed"
    assert out["kill_attempted"] is True
