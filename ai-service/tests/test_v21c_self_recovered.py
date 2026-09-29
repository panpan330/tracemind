"""V2.1-C 提交②:resolved 复核 + SELF_RECOVERED 终态全链路。

覆盖(含用户验收钉住项):
- resolved + 未执行写动作 + 信号后新鲜样本已恢复 → SELF_RECOVERED(不宣告根因);
- 仍异常 → 继续调查;已执行写动作 → 不宣告恢复;非 RESOLVED → 零开销直通;
- 后端 SSE 终态、Run 收尾/回放(outcome=self_recovered)与终态键释放。
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import FixExecution, Incident, utcnow
from app.repositories import run_repo
from app.services import recovery_signal
from app.agent import nodes
from app.api import stream


def _incident(*, alert_status="RESOLVED", service_ref="order-service",
              operation_ref="ORDER_CREATE") -> int:
    with Session(get_control_engine()) as s:
        inc = Incident(title=f"sr-{uuid.uuid4().hex[:6]}", severity="high",
                       service_ref=service_ref,
                       affected_operation_ref=operation_ref,
                       source="alertmanager", alert_name="OrderOperationP95High",
                       environment="demo", alert_status=alert_status,
                       lifecycle_status="OPEN")
        s.add(inc)
        s.commit()
        s.refresh(inc)
        return inc.id


def _run(incident_id: int):
    run = run_repo.create_run(incident_id, active_run_key=f"incident:{incident_id}")
    run_repo.update_run_status(run.id, "investigating")
    return run


def _resolved_at(incident_id: int, when: datetime) -> None:
    """写入关联实例的 resolved_at(自愈判定的信号时刻来源)。"""
    key = f"alertmanager:fp-{uuid.uuid4().hex[:8]}:{when.isoformat()}"
    with Session(get_control_engine()) as s:
        s.execute(text(
            "INSERT INTO alert_instance (alert_instance_key, source, "
            "external_fingerprint, starts_at, current_status, resolved_at, "
            "last_received_at, last_event_id, version) "
            "VALUES (:k, 'alertmanager', :fp, :sa, 'RESOLVED', :ra, :ra, 1, 2)"),
            {"k": key, "fp": key.split(":")[1], "sa": when - timedelta(minutes=5),
             "ra": when})
        s.execute(text("INSERT INTO incident_alert (incident_id, alert_instance_key) "
                       "VALUES (:i, :k)"), {"i": incident_id, "k": key})
        s.commit()


def _signal(status, *, source=None, threshold=None, p95=None):
    return recovery_signal.RecoverySignal(
        status=status, source=source, threshold_ms=threshold, p95_ms=p95,
        sample_count=100, window_seconds=60,
        evaluated_at=datetime(2026, 9, 28, 6, 5, 0), post_signal_window=True)


@pytest.fixture
def patched(monkeypatch):
    calls = {}

    def fake(service_ref, operation_ref, signal_at, *, baseline, **kw):
        calls.update({"service_ref": service_ref, "operation_ref": operation_ref,
                      "signal_at": signal_at, "baseline": baseline})
        return calls["result"]

    monkeypatch.setattr(recovery_signal, "measure_post_signal_p95", fake)
    return calls


def test_not_resolved_is_zero_cost_passthrough(patched):
    """alert_status 非 RESOLVED → 直通,不发起任何指标查询。"""
    inc_id = _incident(alert_status="FIRING")
    run = _run(inc_id)
    out = nodes.resolved_recheck({"incident_id": inc_id, "run_id": run.id,
                                  "service_ref": "order-service"})
    assert out == {}
    assert patched == {}


def test_resolved_with_write_executed_does_not_declare_recovery(patched):
    """已执行写动作 → 不宣告自愈(写后恢复由恢复验证判定)。"""
    inc_id = _incident()
    run = _run(inc_id)
    with Session(get_control_engine()) as s:
        s.add(FixExecution(incident_id=inc_id, fix_proposal_id=1, approval_id=1,
                           idempotency_key=f"k-{uuid.uuid4().hex[:8]}",
                           status="succeeded", result={}))
        s.commit()
    out = nodes.resolved_recheck({"incident_id": inc_id, "run_id": run.id,
                                  "service_ref": "order-service"})
    assert out == {"resolved_recheck": {"status": "write_already_executed"}}
    assert patched == {}                             # 不查指标


def test_resolved_still_degraded_continues(patched, monkeypatch):
    """信号仍异常 → 不宣告自愈,继续调查(diagnose)。"""
    inc_id = _incident()
    run = _run(inc_id)
    _resolved_at(inc_id, utcnow() - timedelta(minutes=5))
    patched["result"] = _signal(recovery_signal.STATUS_NOT_RECOVERED,
                                source=recovery_signal.SOURCE_SLO,
                                threshold=100.0, p95=300.0)
    out = nodes.resolved_recheck({"incident_id": inc_id, "run_id": run.id,
                                  "service_ref": "order-service",
                                  "affected_operation_ref": "ORDER_CREATE"})
    assert out.get("status") != "self_recovered"
    assert out["resolved_recheck"]["status"] == "still_degraded"
    assert patched["service_ref"] == "order-service"
    assert patched["operation_ref"] == "ORDER_CREATE"


def test_resolved_and_recovered_declares_self_recovered(patched):
    """resolved + 未写 + 信号后新鲜样本已恢复 → SELF_RECOVERED(终态),并落 SSE + 回放。"""
    inc_id = _incident()
    run = _run(inc_id)
    _resolved_at(inc_id, utcnow() - timedelta(minutes=5))
    patched["result"] = _signal(recovery_signal.STATUS_RECOVERED,
                                source=recovery_signal.SOURCE_SLO,
                                threshold=100.0, p95=12.0)
    out = nodes.resolved_recheck({"incident_id": inc_id, "run_id": run.id,
                                  "service_ref": "order-service",
                                  "affected_operation_ref": "ORDER_CREATE"})
    assert out["status"] == "self_recovered"
    assert out["termination_reason"] is None
    assert out["resolved_recheck"]["status"] == "self_recovered"
    assert out["resolved_recheck"]["source"] == "SLO"
    with Session(get_control_engine()) as s:
        ev = s.execute(text("SELECT event_type FROM incident_event WHERE incident_id=:i "
                            "ORDER BY sequence DESC LIMIT 1"), {"i": inc_id}).scalar()
        step = s.execute(text("SELECT step_type, step_outcome FROM incident_replay_step "
                              "WHERE incident_id=:i ORDER BY id DESC LIMIT 1"),
                         {"i": inc_id}).fetchone()
    assert ev == "run.self_recovered"
    assert step.step_type == "ALERT_RESOLVED_RECHECK"


def test_inconclusive_signal_does_not_declare_recovery(patched):
    """信号 INCONCLUSIVE(如信号后无流量)→ 不宣告自愈,继续调查。"""
    inc_id = _incident()
    run = _run(inc_id)
    _resolved_at(inc_id, utcnow() - timedelta(minutes=5))
    patched["result"] = recovery_signal.RecoverySignal(
        status=recovery_signal.STATUS_INCONCLUSIVE, sample_count=0,
        window_seconds=60, reason="insufficient_post_signal_requests")
    out = nodes.resolved_recheck({"incident_id": inc_id, "run_id": run.id,
                                  "service_ref": "order-service"})
    assert out.get("status") != "self_recovered"
    assert out["resolved_recheck"]["status"] == "recheck_inconclusive"


# ---------- 终态管线:Run 收尾 / 回放 / SSE ----------

def test_self_recovered_is_terminal_and_releases_keys():
    inc_id = _incident()
    run = _run(inc_id)
    assert "self_recovered" in run_repo.TERMINAL_STATUSES
    run_repo.update_run_status(run.id, "self_recovered")
    with Session(get_control_engine()) as s:
        row = s.execute(text("SELECT status, active_run_key, lease_owner, finished_at "
                             "FROM agent_run WHERE id=:i"), {"i": run.id}).fetchone()
    assert row.status == "self_recovered"
    assert row.active_run_key is None and row.lease_owner is None
    assert row.finished_at is not None


def test_stream_treats_self_recovered_as_terminal():
    assert "self_recovered" in stream.TERMINAL_STATUSES


def test_finalize_run_writes_self_recovered_outcome():
    from app.services import runner
    inc_id = _incident()
    run = _run(inc_id)
    runner._finalize_run(inc_id, run.id, "self_recovered", None)
    with Session(get_control_engine()) as s:
        step = s.execute(text("SELECT step_type, step_outcome FROM incident_replay_step "
                             "WHERE incident_id=:i ORDER BY id DESC LIMIT 1"),
                         {"i": inc_id}).fetchone()
    assert step.step_type == "RUN_TERMINATED"
    assert step.step_outcome == "self_recovered"
