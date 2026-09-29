"""V2.1-C 提交①:启动前基线两段式封存(特征测试先行)。

覆盖:历史窗口边界与质量校验、digest {} 与 None 语义、封存 CAS(幂等/竞争/重试)、
Dispatcher 两段式集成、健康基线权威来源切换、快照冻结可恢复。
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.engine import get_control_engine
from app.db.models import Incident
from app.repositories import run_repo
from app.services import baseline_capture, dispatcher, runner
from app.services.baseline_capture import capture_run_baselines
from app.services.run_context import load_snapshot
from app.services import slow_query_service
from tests.test_dispatcher import _drain_stale_queued, _mk_queued, _run_row


def _alert_starts_at() -> datetime:
    return datetime(2026, 9, 28, 5, 0, 0)


class FakePrometheus:
    """可编程假客户端:记录 query_at/sample_count 调用,返回可配置结果。"""

    def __init__(self, *, p95_ms=8.0, qps=10.0, error_rate=0.0, samples=120):
        self.p95_ms = p95_ms
        self.qps = qps
        self.error_rate = error_rate
        self.samples = samples
        self.query_at_calls = []
        self.count_calls = []

    def query_at(self, template_id, labels, window_seconds, at_time):
        self.query_at_calls.append((template_id, dict(labels), window_seconds, at_time))
        if template_id == "HTTP_SERVER_P95_V1":
            value = self.p95_ms / 1000.0
        elif template_id == "HTTP_SERVER_QPS_V1":
            value = self.qps
        else:
            value = self.error_rate
        return [{"metric": {}, "value": [at_time, str(value)]}]

    def sample_count(self, template_id, labels, start, end):
        self.count_calls.append((template_id, dict(labels), start, end))
        return self.samples


@pytest.fixture
def fake_prom(monkeypatch):
    fake = FakePrometheus()
    monkeypatch.setattr(baseline_capture, "_prometheus_client", lambda: fake)
    return fake


def _capture_status(run_id):
    with Session(get_control_engine()) as s:
        return s.scalar(text(
            "SELECT baseline_capture_status FROM agent_run WHERE id=:i"),
            {"i": run_id})


def _mk_alert_queued(**kw):
    """构造 alertmanager queued Run(含冻结快照)。"""
    _drain_stale_queued()
    return _mk_queued(drain=False, **kw)


# ---------- 历史窗口采集与质量校验 ----------

def test_capture_window_bounds_and_quality_ok(fake_prom):
    """窗口严格 [startsAt-10m, startsAt-1m]:instant 查询 time=窗口终点、
    rate 窗口=两端差值;样本数达标且 p95 低于健康阈值 → OK。"""
    inc_id, run_id = _mk_alert_queued()
    run = run_repo.get_run(run_id)
    out = capture_run_baselines(run, alert_starts_at=_alert_starts_at())

    assert out.status == "OK"
    assert out.healthy_metrics["p95_ms"] == 8.0
    assert out.healthy_metrics["sample_count"] == 120
    # 窗口边界:终点 = startsAt-60s;rate 窗口 = 600s-60s = 540s
    end = _alert_starts_at() - timedelta(seconds=settings.baseline_window_end_offset_s)
    win = settings.baseline_window_before_start_s - settings.baseline_window_end_offset_s
    tpl, labels, window, at_time = fake_prom.query_at_calls[0]
    assert tpl == "HTTP_SERVER_P95_V1"
    assert window == win
    from datetime import timezone as _tz
    assert abs(at_time - end.replace(tzinfo=_tz.utc).timestamp()) < 1   # 评估点 = 窗口终点(UTC)
    _, _, cstart, cend = fake_prom.count_calls[0]
    assert abs(cstart - (end - timedelta(seconds=win)).replace(tzinfo=_tz.utc).timestamp()) < 1
    assert abs(cend - end.replace(tzinfo=_tz.utc).timestamp()) < 1
    # 服务/操作来自冻结快照(与服务端映射的告警标签一致)
    assert labels["service"] == "inventory-service"


def test_capture_insufficient_samples(fake_prom):
    """样本数不足 → INSUFFICIENT,且不产出健康指标(不伪造基线)。"""
    fake_prom.samples = 5
    inc_id, run_id = _mk_alert_queued()
    run = run_repo.get_run(run_id)
    out = capture_run_baselines(run, alert_starts_at=_alert_starts_at())
    assert out.status == "INSUFFICIENT"
    assert out.healthy_metrics is None


def test_capture_unhealthy_window_p95_over_threshold(fake_prom):
    """窗口内 p95 已超健康阈值(故障早于告警)→ INSUFFICIENT。"""
    fake_prom.p95_ms = settings.baseline_max_p95_ms * 3
    inc_id, run_id = _mk_alert_queued()
    run = run_repo.get_run(run_id)
    out = capture_run_baselines(run, alert_starts_at=_alert_starts_at())
    assert out.status == "INSUFFICIENT"
    assert out.healthy_metrics is None


def test_capture_prometheus_unreachable_is_capture_failed(monkeypatch):
    """Prometheus 不可达 → CAPTURE_FAILED(可重试),不写健康指标。"""
    inc_id, run_id = _mk_alert_queued()
    run = run_repo.get_run(run_id)

    def _boom():
        raise RuntimeError("prometheus down")

    monkeypatch.setattr(baseline_capture, "_prometheus_client", _boom)
    out = capture_run_baselines(run, alert_starts_at=_alert_starts_at())
    assert out.status == "CAPTURE_FAILED"
    assert out.healthy_metrics is None


# ---------- digest 快照 {} 与 None 语义 ----------

def test_digest_empty_snapshot_is_valid_not_insufficient():
    """{} 是有效空快照:增量按"当前值即增量"计算;None 才是未采集(fail closed)。"""
    inc = _mk_incident()
    baseline = {}
    run = run_repo.create_run(inc.id, baseline=baseline)
    current = {"SELECT sku FROM inventory": {"count": 3000, "total_latency_us": 0,
                                             "rows_examined": 5000}}
    orig = slow_query_service._fetch_current_digests
    slow_query_service._fetch_current_digests = lambda: current
    try:
        out = slow_query_service.list_expensive_digests(inc.id, agent_run_id=run.id)
    finally:
        slow_query_service._fetch_current_digests = orig
    top = out[0]
    assert top["rows_examined_delta"] == 5000        # 当前值即增量


def _mk_incident():
    with Session(get_control_engine()) as s:
        inc = Incident(title=f"cap-{uuid.uuid4().hex[:6]}", severity="high",
                       service_ref="order-service",
                       affected_operation_ref="ORDER_CREATE")
        s.add(inc)
        s.commit()
        s.refresh(inc)
        return inc


# ---------- 封存 CAS(单事务:校验+封存+调度) ----------

def _ok_capture():
    from app.services.baseline_capture import BaselineCapture
    return BaselineCapture(
        status="OK", digest_baseline={"SELECT d": {"count": 1, "total_latency_us": 1,
                                                   "rows_examined": 1}},
        healthy_metrics={"p95_ms": 50.0, "qps": 10.0, "error_rate": 0.0,
                         "sample_count": 120},
        window_start=_alert_starts_at() - timedelta(seconds=600),
        window_end=_alert_starts_at() - timedelta(seconds=60))


def test_seal_cas_success_writes_all_and_dispatches():
    inc_id, run_id = _mk_alert_queued()
    now = datetime.utcnow()
    run_repo.claim_queued_run("owner-a", lease_seconds=30, now=now)
    with Session(get_control_engine()) as s:   # 生产中由网关聚合事务写入
        s.execute(text("UPDATE agent_run SET active_run_key=:k WHERE id=:i"),
                  {"k": f"incident:{inc_id}", "i": run_id})
        s.commit()
    sealed = run_repo.seal_run_baselines_and_dispatch(
        run_id, owner="owner-a", now=now, capture=_ok_capture())
    assert sealed is True
    with Session(get_control_engine()) as s:
        r = s.execute(text("SELECT status, dispatch_status, baseline_capture_status, "
                           "active_run_key FROM agent_run WHERE id=:i"),
                      {"i": run_id}).fetchone()
        snap = s.execute(text("SELECT run_context_snapshot_json FROM agent_run "
                              "WHERE id=:i"), {"i": run_id}).scalar()
        inc = s.execute(text("SELECT baseline_window_start, baseline_window_end, "
                             "baseline_metrics_json, baseline_quality, "
                             "auto_run_started_at FROM incident WHERE id=:i"),
                        {"i": inc_id}).fetchone()
    assert r.status == "investigating" and r.dispatch_status == "DISPATCHED"
    assert r.baseline_capture_status == "OK"
    assert r.active_run_key == f"incident:{inc_id}"      # 活动键不变(启动后才有)
    import json as _json
    if isinstance(snap, str):
        snap = _json.loads(snap)
    assert snap["baseline_ref"] and snap["baseline_quality"] == "OK"
    assert snap["healthy_baseline_ref"]["p95_ms"] == 50.0
    assert snap["baseline_window"]["start"]
    assert inc.baseline_quality == "OK" and inc.baseline_metrics_json
    assert inc.baseline_window_start is not None and inc.auto_run_started_at is not None


def test_seal_cas_rejects_when_not_claimed_by_owner():
    inc_id, run_id = _mk_alert_queued()
    now = datetime.utcnow()
    # 未领取(READY、无租约)→ 拒绝
    assert run_repo.seal_run_baselines_and_dispatch(
        run_id, owner="owner-a", now=now, capture=_ok_capture()) is False
    row = _run_row(run_id)
    assert row.status == "queued" and row.dispatch_status == "READY"


def test_seal_cas_rejects_after_started():
    inc_id, run_id = _mk_alert_queued()
    now = datetime.utcnow()
    run_repo.update_run_status(run_id, "investigating")   # 已启动
    assert run_repo.seal_run_baselines_and_dispatch(
        run_id, owner="owner-a", now=now, capture=_ok_capture()) is False


def test_seal_cas_rejects_expired_lease():
    inc_id, run_id = _mk_alert_queued()
    from datetime import timedelta as td
    now = datetime.utcnow()
    run_repo.claim_queued_run("owner-a", lease_seconds=30, now=now)
    expired = now + td(minutes=5)
    assert run_repo.seal_run_baselines_and_dispatch(
        run_id, owner="owner-a", now=expired, capture=_ok_capture()) is False


def test_seal_double_is_rejected_and_idempotent():
    """单次封存:OK 后重复封存被 CAS 拒绝,且不覆盖已封存值。"""
    inc_id, run_id = _mk_alert_queued()
    now = datetime.utcnow()
    run_repo.claim_queued_run("owner-a", lease_seconds=30, now=now)
    assert run_repo.seal_run_baselines_and_dispatch(
        run_id, owner="owner-a", now=now, capture=_ok_capture()) is True
    other = _ok_capture()
    other.digest_baseline = {"OTHER": {"count": 9, "total_latency_us": 9,
                                       "rows_examined": 9}}
    assert run_repo.seal_run_baselines_and_dispatch(
        run_id, owner="owner-a", now=now, capture=other) is False
    run = run_repo.get_run(run_id)
    assert run.incident_digest_baseline["SELECT d"]["count"] == 1   # 未被覆盖


def test_seal_allows_retry_after_capture_failed():
    """CAPTURE_FAILED 可按同一封存 CAS 重试(重试条件 ≡ 封存条件)。"""
    inc_id, run_id = _mk_alert_queued()
    now = datetime.utcnow()
    with Session(get_control_engine()) as s:
        s.execute(text("UPDATE agent_run SET baseline_capture_status='CAPTURE_FAILED' "
                       "WHERE id=:i"), {"i": run_id})
        s.commit()
    run_repo.claim_queued_run("owner-b", lease_seconds=30, now=now)
    assert run_repo.seal_run_baselines_and_dispatch(
        run_id, owner="owner-b", now=now, capture=_ok_capture()) is True


def test_seal_insufficient_is_final_and_no_healthy_baseline():
    """INSUFFICIENT 同样是终值(单次封存):健康基线不落库(不伪造)。"""
    inc_id, run_id = _mk_alert_queued()
    now = datetime.utcnow()
    run_repo.claim_queued_run("owner-a", lease_seconds=30, now=now)
    cap = _ok_capture()
    cap.status = "INSUFFICIENT"
    cap.healthy_metrics = None
    assert run_repo.seal_run_baselines_and_dispatch(
        run_id, owner="owner-a", now=now, capture=cap) is True
    run = run_repo.get_run(run_id)
    snap = load_snapshot(run)
    assert snap.baseline_quality == "INSUFFICIENT"
    assert snap.healthy_baseline_ref is None            # 不伪造健康基线
    with Session(get_control_engine()) as s:
        inc = s.execute(text("SELECT baseline_metrics_json, baseline_quality "
                             "FROM incident WHERE id=:i"), {"i": inc_id}).fetchone()
    assert inc.baseline_metrics_json is None
    assert inc.baseline_quality == "INSUFFICIENT"


# ---------- Dispatcher 两段式集成 ----------

@pytest.mark.asyncio
async def test_dispatcher_capture_failure_reverts_and_retries(monkeypatch):
    """采集异常 → CAPTURE_FAILED + 回退 READY(不启动图),下一轮同 CAS 重试成功。"""
    inc_id, run_id = _mk_alert_queued()
    attempts = {"n": 0}

    def flaky_capture(run, alert_starts_at=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("prometheus down")
        return _ok_capture()

    monkeypatch.setattr(baseline_capture, "capture_run_baselines", flaky_capture)
    started = []

    async def recording_start(incident_id, run_id_, thread_id):
        started.append(run_id_)

    monkeypatch.setattr(runner, "start_investigation", recording_start)

    await dispatcher.dispatch_once()
    assert attempts["n"] == 1 and started == []          # 未启动
    row = _run_row(run_id)
    assert row.status == "queued" and row.dispatch_status == "READY"
    assert _capture_status(run_id) == "CAPTURE_FAILED"

    await dispatcher.dispatch_once()                     # 重试(同 CAS)
    assert attempts["n"] == 2 and started == [run_id]
    assert _run_row(run_id).status == "investigating"
    assert _capture_status(run_id) == "OK"


@pytest.mark.asyncio
async def test_dispatcher_seal_race_consumes_capacity(monkeypatch):
    """封存 CAS 失败(租约被回收/状态漂移)→ 不启动图,不无限循环。"""
    inc_id, run_id = _mk_alert_queued()
    monkeypatch.setattr(baseline_capture, "capture_run_baselines",
                        lambda run, alert_starts_at=None: _ok_capture())

    started2 = []

    async def recording_start(incident_id, run_id_, thread_id):
        started2.append(run_id_)

    monkeypatch.setattr(runner, "start_investigation", recording_start)

    # 竞争注入:封存前另一 owner 已完成"封存+调度"(状态漂移)→ 本 owner CAS 必失败
    orig_seal = run_repo.seal_run_baselines_and_dispatch

    def racing_seal(run_id_, owner_, now_, **kw):
        with Session(get_control_engine()) as s:
            s.execute(text(
                "UPDATE agent_run SET dispatch_status='DISPATCHED', "
                "status='investigating', lease_owner='other-owner' WHERE id=:i"),
                {"i": run_id_})
            s.commit()
        return orig_seal(run_id_, owner_, now_, **kw)

    monkeypatch.setattr(run_repo, "seal_run_baselines_and_dispatch", racing_seal)
    await dispatcher.dispatch_once()
    row = _run_row(run_id)
    assert row.dispatch_status == "DISPATCHED" and row.lease_owner == "other-owner"
    assert started2 == []                                # 本 owner 未启动图(无重复执行)


@pytest.mark.asyncio
async def test_dispatcher_sealed_baseline_reaches_initial_state(monkeypatch):
    """端到端:封存的基线进入图初始状态(恢复也只读快照,不回读 Incident)。"""
    inc_id, run_id = _mk_alert_queued()
    monkeypatch.setattr(baseline_capture, "capture_run_baselines",
                        lambda run, alert_starts_at=None: _ok_capture())
    seen = {}

    async def recording_start(incident_id, run_id_, thread_id):
        seen["run"] = run_repo.get_run(run_id_)
        seen["initial"] = runner._initial_state_from_run(seen["run"])

    monkeypatch.setattr(runner, "start_investigation", recording_start)
    await dispatcher.dispatch_once()
    initial = seen["initial"]
    assert initial["baseline_ref"]["SELECT d"]["count"] == 1
    assert initial["healthy_baseline_ref"]["p95_ms"] == 50.0
    # Incident 行基线被改不影响已冻结快照
    with Session(get_control_engine()) as s:
        s.execute(text("UPDATE incident SET baseline_metrics_json=NULL, "
                       "baseline_quality='INSUFFICIENT' WHERE id=:i"), {"i": inc_id})
        s.commit()
    initial2 = runner._initial_state_from_run(run_repo.get_run(run_id))
    assert initial2["healthy_baseline_ref"]["p95_ms"] == 50.0    # 冻结值


# ---------- 健康基线权威来源切换 ----------

def test_snapshot_healthy_source_only_when_quality_ok():
    """healthy_baseline_ref 只在 baseline_quality='OK' 时取 baseline_metrics_json;
    healthy_metrics_baseline(旧列,故障态值)不再作为健康基线来源。"""
    import json
    with Session(get_control_engine()) as s:
        inc = Incident(title=f"src-{uuid.uuid4().hex[:6]}", severity="high",
                       service_ref="order-service",
                       affected_operation_ref="ORDER_CREATE",
                       healthy_metrics_baseline={"p95_ms": 999})   # 旧列故障态值
        s.add(inc)
        s.commit()
        s.refresh(inc)
        inc_id = inc.id
    run = run_repo.create_run(inc_id)
    snap = load_snapshot(run)
    assert snap.healthy_baseline_ref is None            # 旧列不再充当健康基线

    with Session(get_control_engine()) as s:
        s.execute(text("UPDATE incident SET baseline_metrics_json=:m, "
                       "baseline_quality='OK' WHERE id=:i"),
                  {"m": json.dumps({"p95_ms": 50}), "i": inc_id})
        s.commit()
    run2 = run_repo.create_run(inc_id)
    snap2 = load_snapshot(run2)
    assert snap2.healthy_baseline_ref == {"p95_ms": 50}
