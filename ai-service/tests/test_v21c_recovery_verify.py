"""V2.1-C 提交②:恢复验证 —— 统一 HTTP P95 信号、SLO/INCONCLUSIVE、SQL 探针仅作佐证。

覆盖(含用户验收钉住项):
- 通用验证器与锁场景独立验证器使用**同一**恢复信号实现(口径一致);
- 阈值来源:合格基线 / 显式 SLO;不可判定 → INCONCLUSIVE(不宣告恢复也不宣告失败);
- 直接 SQL 耗时只作独立佐证,不参与 recovered 判定;
- 信号时刻 = 修复动作完成时刻(窗口须完全位于信号之后)。
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.capabilities.mysql_blocking_transaction import capability as lock_cap
from app.db.engine import get_control_engine
from app.db.models import FixExecution, Incident
from app.repositories import run_repo
from app.services import recovery_service, recovery_signal


def _incident(*, healthy: dict | None) -> int:
    import json
    with Session(get_control_engine()) as s:
        inc = Incident(title=f"rv-{uuid.uuid4().hex[:6]}", severity="high",
                       service_ref="order-service",
                       affected_operation_ref="ORDER_CREATE")
        s.add(inc)
        s.commit()
        s.refresh(inc)
        inc_id = inc.id
        if healthy is not None:
            s.execute(text("UPDATE incident SET baseline_metrics_json=:m, "
                           "baseline_quality='OK' WHERE id=:i"),
                      {"m": json.dumps(healthy), "i": inc_id})
            s.commit()
    return inc_id


def _fix_execution(incident_id: int, *, created_at=None) -> int:
    with Session(get_control_engine()) as s:
        row = FixExecution(incident_id=incident_id, fix_proposal_id=1, approval_id=1,
                           idempotency_key=f"k-{uuid.uuid4().hex[:10]}",
                           status="succeeded", result={"detail": "index created"})
        if created_at is not None:
            row.created_at = created_at
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id


def _signal(status, *, source=None, threshold=None, p95=None):
    return recovery_signal.RecoverySignal(
        status=status, source=source, threshold_ms=threshold, p95_ms=p95,
        sample_count=100, window_seconds=60,
        evaluated_at=datetime(2026, 9, 28, 6, 5, 0), post_signal_window=True)


@pytest.fixture
def patched_signal(monkeypatch):
    """注入恢复信号(记录调用参数),避免真实观测后端依赖。"""
    calls = {}

    def fake(service_ref, operation_ref, signal_at, *, baseline, **kw):
        calls.update({"service_ref": service_ref, "operation_ref": operation_ref,
                      "signal_at": signal_at, "baseline": baseline})
        return calls["result"]

    monkeypatch.setattr(recovery_signal, "measure_post_signal_p95", fake)
    return calls


def test_verify_recovery_uses_frozen_context_and_baseline_source(patched_signal,
                                                                monkeypatch):
    """信号口径:服务/操作取冻结 RunContext;有合格基线 → 阈值来源=baseline;
    HTTP P95 写入 RecoveryCheck.latency_p95_after(判定信号)。"""
    inc_id = _incident(healthy={"p95_ms": 50})
    run = run_repo.create_run(inc_id)
    fix_id = _fix_execution(inc_id)
    patched_signal["result"] = _signal(recovery_signal.STATUS_RECOVERED,
                                       source=recovery_signal.SOURCE_BASELINE,
                                       threshold=60.0, p95=25.0)
    monkeypatch.setattr(recovery_service, "_index_checks",
                        lambda execution: (True, True, 10))
    out = recovery_service.verify_recovery(inc_id, fix_id, agent_run_id=run.id)
    assert patched_signal["service_ref"] == "order-service"
    assert patched_signal["operation_ref"] == "ORDER_CREATE"
    assert patched_signal["baseline"] == {"p95_ms": 50}      # 冻结快照基线
    assert out["status"] == "recovered"
    assert out["threshold_source"] == "baseline"
    with Session(get_control_engine()) as s:
        chk = s.execute(text("SELECT status, latency_p95_after FROM recovery_check "
                             "WHERE incident_id=:i ORDER BY id DESC LIMIT 1"),
                        {"i": inc_id}).fetchone()
    assert chk.status == "recovered"
    assert chk.latency_p95_after == 25                        # HTTP P95(非 SQL 探针)


def test_verify_recovery_inconclusive_is_not_recovered(patched_signal, monkeypatch):
    """信号 INCONCLUSIVE → RecoveryCheck.status=INCONCLUSIVE(既不恢复也不失败)。"""
    inc_id = _incident(healthy=None)
    run = run_repo.create_run(inc_id)
    fix_id = _fix_execution(inc_id)
    patched_signal["result"] = recovery_signal.RecoverySignal(
        status=recovery_signal.STATUS_INCONCLUSIVE, sample_count=0,
        window_seconds=60, reason="insufficient_post_signal_requests")
    monkeypatch.setattr(recovery_service, "_index_checks",
                        lambda execution: (True, True, 10))
    out = recovery_service.verify_recovery(inc_id, fix_id, agent_run_id=run.id)
    assert out["status"] == "INCONCLUSIVE"
    assert out["signal"]["reason"] == "insufficient_post_signal_requests"
    with Session(get_control_engine()) as s:
        chk = s.execute(text("SELECT status FROM recovery_check WHERE incident_id=:i "
                             "ORDER BY id DESC LIMIT 1"), {"i": inc_id}).fetchone()
    assert chk.status == "INCONCLUSIVE"


def test_verify_recovery_not_recovered(patched_signal, monkeypatch):
    inc_id = _incident(healthy={"p95_ms": 50})
    run = run_repo.create_run(inc_id)
    fix_id = _fix_execution(inc_id)
    patched_signal["result"] = _signal(recovery_signal.STATUS_NOT_RECOVERED,
                                       source=recovery_signal.SOURCE_BASELINE,
                                       threshold=60.0, p95=180.0)
    monkeypatch.setattr(recovery_service, "_index_checks",
                        lambda execution: (True, True, 10))
    out = recovery_service.verify_recovery(inc_id, fix_id, agent_run_id=run.id)
    assert out["status"] == "not_recovered"
    assert out["latency_p95_after"] == 180


def test_no_fail_open_without_qualified_baseline(patched_signal, monkeypatch):
    """无合格基线 → 走显式 SLO(来源=SLO),绝不"默认通过"。"""
    inc_id = _incident(healthy=None)
    run = run_repo.create_run(inc_id)
    fix_id = _fix_execution(inc_id)
    patched_signal["result"] = _signal(recovery_signal.STATUS_NOT_RECOVERED,
                                       source=recovery_signal.SOURCE_SLO,
                                       threshold=100.0, p95=250.0)
    monkeypatch.setattr(recovery_service, "_index_checks",
                        lambda execution: (True, True, 10))
    out = recovery_service.verify_recovery(inc_id, fix_id, agent_run_id=run.id)
    assert patched_signal["baseline"] is None
    assert out["status"] == "not_recovered"          # 不再因基线缺失而"视为通过"
    assert out["threshold_source"] == "SLO"


def test_sql_probe_is_supporting_evidence_only(patched_signal, monkeypatch):
    """直接 SQL 探针极慢(9999ms)但 HTTP P95 已恢复 → 仍判 recovered;探针仅入佐证字段。"""
    inc_id = _incident(healthy={"p95_ms": 50})
    run = run_repo.create_run(inc_id)
    fix_id = _fix_execution(inc_id)
    patched_signal["result"] = _signal(recovery_signal.STATUS_RECOVERED,
                                       source=recovery_signal.SOURCE_BASELINE,
                                       threshold=60.0, p95=20.0)
    monkeypatch.setattr(recovery_service, "_index_checks",
                        lambda execution: (True, True, 10))
    monkeypatch.setattr(recovery_service, "_probe_p95_ms", lambda: 9999)
    out = recovery_service.verify_recovery(inc_id, fix_id, agent_run_id=run.id)
    assert out["status"] == "recovered"
    assert out["supporting_probe_p95_ms"] == 9999     # 独立佐证,不参与判定


def test_signal_at_is_fix_completion_time(patched_signal, monkeypatch):
    """信号时刻 = 修复动作完成时刻(窗口须完全位于其后)。"""
    inc_id = _incident(healthy={"p95_ms": 50})
    run = run_repo.create_run(inc_id)
    fixed_at = datetime(2026, 9, 28, 5, 59, 0)
    fix_id = _fix_execution(inc_id, created_at=fixed_at)
    patched_signal["result"] = _signal(recovery_signal.STATUS_RECOVERED,
                                       source=recovery_signal.SOURCE_BASELINE,
                                       threshold=60.0, p95=20.0)
    monkeypatch.setattr(recovery_service, "_index_checks",
                        lambda execution: (True, True, 10))
    recovery_service.verify_recovery(inc_id, fix_id, agent_run_id=run.id)
    assert patched_signal["signal_at"] == fixed_at


# ---------- 锁场景独立验证器:同一口径 ----------

def test_lock_verifier_uses_same_recovery_signal(monkeypatch):
    """锁场景六项验证在"目标等待消失 + 探测通过"后,必须再过同一 HTTP P95 信号。"""
    calls = {}

    def fake(service_ref, operation_ref, signal_at, *, baseline, **kw):
        calls.update({"service_ref": service_ref, "operation_ref": operation_ref})
        return recovery_signal.RecoverySignal(
            status=recovery_signal.STATUS_NOT_RECOVERED,
            source=recovery_signal.SOURCE_SLO, threshold_ms=100.0, p95_ms=300.0,
            sample_count=50, window_seconds=60, post_signal_window=True)

    monkeypatch.setattr(recovery_signal, "measure_post_signal_p95", fake)
    monkeypatch.setattr(lock_cap, "_poll_target_lock_gone",
                        lambda deadline_s=60: True)
    monkeypatch.setattr(lock_cap, "run_probe_batches",
                        lambda state, batches=3: [{"success": True}] * batches)
    state = {"incident_id": 999999, "run_id": 1, "service_ref": "order-service",
             "affected_operation_ref": "ORDER_CREATE",
             "healthy_baseline_ref": None,
             "fix_execution": {"created_at": "2026-09-28T05:59:00"}}
    out = lock_cap.verify_lock_recovery(state)
    assert calls["service_ref"] == "order-service"
    assert out["status"] == "needs_human"                 # P95 未恢复 → 不宣告恢复
    assert out["recovery"]["termination_reason"] == "recovery_p95_not_recovered"


def test_lock_verifier_recovered_when_signal_recovered(monkeypatch):
    monkeypatch.setattr(recovery_signal, "measure_post_signal_p95",
                        lambda *a, **kw: recovery_signal.RecoverySignal(
                            status=recovery_signal.STATUS_RECOVERED,
                            source=recovery_signal.SOURCE_SLO, threshold_ms=100.0,
                            p95_ms=20.0, sample_count=50, window_seconds=60,
                            post_signal_window=True))
    monkeypatch.setattr(lock_cap, "_poll_target_lock_gone",
                        lambda deadline_s=60: True)
    monkeypatch.setattr(lock_cap, "run_probe_batches",
                        lambda state, batches=3: [{"success": True}] * batches)
    state = {"incident_id": 999998, "run_id": 1, "service_ref": "order-service",
             "affected_operation_ref": "ORDER_CREATE",
             "healthy_baseline_ref": None,
             "fix_execution": {"created_at": "2026-09-28T05:59:00"}}
    out = lock_cap.verify_lock_recovery(state)
    assert out["status"] == "recovered"
