"""V2.1-C 提交③:episode 关闭 + 告警生命周期 SSE 事件 + API 暴露(特征测试先行)。

覆盖(计划 v2 §九/§十一,§十四 测试 24-29):
- resolved 且存在非终态 Run → 不关闭、不清 open_group_key(关闭条件③按 status 判定);
- Run 终态提交后(T4)→ CLOSED + open_group_key=NULL + closed_at;
- 手动 Incident(alert_status NULL)永不关闭;幂等:已 CLOSED → 跳过;
- 关闭后同 fingerprint 更晚 startsAt FIRING → 新 episode;未关闭时同组 → 仍合并;
- 生命周期事件与聚合同事务落库(created_from_alert/alert_merged/alert_resolved),
  无 alert.received;run.auto_started 封存调度成功后恰一次;
- 真实线程并发:resolved 与 Run 终态交错,无死锁且最终收敛 CLOSED(锁序与网关一致);
- /api/incidents 暴露告警生命周期与基线质量字段。
"""
import threading
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.incident_gateway import service as gateway_service
from app.incident_gateway.schemas import AlertmanagerWebhookIn
from app.repositories import run_repo
from app.services import baseline_capture, dispatcher, incident_lifecycle, runner
from app.services.incident_lifecycle import close_incident_if_resolved
from tests.test_v21b_closure import _payload, _process, _purge_demo_group


def _incident_row(inc_id):
    with Session(get_control_engine()) as s:
        return s.execute(text(
            "SELECT id, alert_status, lifecycle_status, open_group_key, closed_at, "
            "occurrence_count, baseline_quality FROM incident WHERE id=:i"),
            {"i": inc_id}).fetchone()


def _incident_by_fp(fp):
    """同 fingerprint 可能跨多个 episode:取**最新实例**(最晚 starts_at)所属 Incident。"""
    with Session(get_control_engine()) as s:
        return s.execute(text(
            "SELECT i.id FROM incident i JOIN incident_alert ia ON ia.incident_id=i.id "
            "JOIN alert_instance ai ON ai.alert_instance_key=ia.alert_instance_key "
            "WHERE ai.external_fingerprint=:f "
            "ORDER BY ai.starts_at DESC, i.id DESC LIMIT 1"), {"f": fp}).scalar()


def _links(inc_id):
    with Session(get_control_engine()) as s:
        return s.execute(text(
            "SELECT alert_instance_key FROM incident_alert WHERE incident_id=:i"),
            {"i": inc_id}).fetchall()


def _events(inc_id):
    import json
    with Session(get_control_engine()) as s:
        rows = s.execute(text(
            "SELECT sequence, event_type, payload FROM incident_event "
            "WHERE incident_id=:i ORDER BY sequence"), {"i": inc_id}).fetchall()
    out = []
    for r in rows:
        payload = r.payload
        if isinstance(payload, str):
            payload = json.loads(payload)
        out.append((r.sequence, r.event_type, payload))
    return out


def _queued_run(inc_id):
    with Session(get_control_engine()) as s:
        return s.execute(text(
            "SELECT id, status FROM agent_run WHERE incident_id=:i ORDER BY id"),
            {"i": inc_id}).first()


# ---------- 关闭条件 ----------

def test_resolved_with_active_run_keeps_episode_open():
    """Run 仍非终态(queued)→ resolved 不关闭、不清 open_group_key(条件③)。"""
    _purge_demo_group()
    fp = uuid.uuid4().hex[:12]
    _process(_payload(fp=fp))
    inc_id = _incident_by_fp(fp)
    run = _queued_run(inc_id)
    assert run is not None and run.status == "queued"      # 非终态

    _process(_payload(fp=fp, status="resolved",
                      ends_at="2026-09-06T06:00:00.000Z"))
    row = _incident_row(inc_id)
    assert row.alert_status == "RESOLVED"
    assert row.lifecycle_status == "OPEN"                  # 不关闭
    assert row.open_group_key is not None                  # 不清键
    assert row.closed_at is None
    _purge_demo_group()


def test_closes_when_run_terminal_after_resolved():
    """Run 终态提交后(T4)→ CLOSED + open_group_key=NULL + closed_at。"""
    _purge_demo_group()
    fp = uuid.uuid4().hex[:12]
    _process(_payload(fp=fp))
    inc_id = _incident_by_fp(fp)
    run = _queued_run(inc_id)
    _process(_payload(fp=fp, status="resolved",
                      ends_at="2026-09-06T06:00:00.000Z"))
    run_repo.update_run_status(run.id, "failed")           # T3 → T4 关闭尝试
    row = _incident_row(inc_id)
    assert row.lifecycle_status == "CLOSED"
    assert row.open_group_key is None
    assert row.closed_at is not None
    _purge_demo_group()


def test_manual_incident_never_closes():
    """手动 Incident(alert_status NULL)不属于告警 episode → 终态也不关闭。"""
    from app.db.models import Incident
    with Session(get_control_engine()) as s:
        inc = Incident(title=f"manual-{uuid.uuid4().hex[:6]}", severity="high",
                       service_ref="inventory-service",
                       affected_operation_ref="INVENTORY_LOOKUP")
        s.add(inc)
        s.commit()
        s.refresh(inc)
        inc_id = inc.id
    run = run_repo.create_run(inc_id)
    run_repo.update_run_status(run.id, "failed")
    row = _incident_row(inc_id)
    assert row.lifecycle_status != "CLOSED"      # 手动 Incident 无告警 episode,不关闭
    assert row.closed_at is None


def test_close_is_idempotent():
    _purge_demo_group()
    fp = uuid.uuid4().hex[:12]
    _process(_payload(fp=fp))
    inc_id = _incident_by_fp(fp)
    _process(_payload(fp=fp, status="resolved",
                      ends_at="2026-09-06T06:00:00.000Z"))
    run = _queued_run(inc_id)
    run_repo.update_run_status(run.id, "failed")
    assert _incident_row(inc_id).lifecycle_status == "CLOSED"
    assert close_incident_if_resolved(inc_id) is False     # 已 CLOSED → 跳过
    assert _incident_row(inc_id).lifecycle_status == "CLOSED"
    _purge_demo_group()


# ---------- episode 边界 ----------

def test_new_episode_fires_after_close():
    """关闭后同 fingerprint 更晚 startsAt FIRING → 新 episode(新 Incident + 新 Run)。"""
    _purge_demo_group()
    fp = uuid.uuid4().hex[:12]
    _process(_payload(fp=fp, starts_at="2026-09-06T05:00:00.000Z"))
    inc1 = _incident_by_fp(fp)
    _process(_payload(fp=fp, starts_at="2026-09-06T05:00:00.000Z", status="resolved",
                      ends_at="2026-09-06T06:00:00.000Z"))
    run1 = _queued_run(inc1)
    run_repo.update_run_status(run1.id, "needs_human")
    assert _incident_row(inc1).lifecycle_status == "CLOSED"

    _process(_payload(fp=fp, starts_at="2026-09-06T08:00:00.000Z"))  # 更晚 startsAt
    inc2 = _incident_by_fp(fp)
    assert inc2 != inc1                                    # 新 episode
    row2 = _incident_row(inc2)
    assert row2.lifecycle_status == "OPEN" and row2.alert_status == "FIRING"
    assert row2.open_group_key is not None
    assert _queued_run(inc2) is not None                   # 新 queued Run
    row1 = _incident_row(inc1)
    assert row1.lifecycle_status == "CLOSED"               # 旧 episode 不复活
    _purge_demo_group()


def test_same_group_merges_while_open():
    """未关闭时同组新 FIRING(不同 fingerprint)→ 仍合并进同一 Incident。"""
    _purge_demo_group()
    fp1, fp2 = uuid.uuid4().hex[:12], uuid.uuid4().hex[:12]
    _process(_payload(fp=fp1, starts_at="2026-09-06T05:00:00.000Z"))
    inc1 = _incident_by_fp(fp1)
    _process(_payload(fp=fp2, starts_at="2026-09-06T05:30:00.000Z"))
    assert _incident_by_fp(fp2) == inc1                    # 合并,不新建
    assert len(_links(inc1)) == 2
    assert _incident_row(inc1).occurrence_count == 2
    _purge_demo_group()


# ---------- 生命周期事件 ----------

def test_lifecycle_events_in_aggregation_transaction():
    """4 类事件与聚合同事务落库:created_from_alert / alert_merged / alert_resolved
    (recovered: false);不发 alert.received(D4:原始告警由 alert_event 留痕)。"""
    _purge_demo_group()
    fp1, fp2 = uuid.uuid4().hex[:12], uuid.uuid4().hex[:12]
    _process(_payload(fp=fp1, starts_at="2026-09-06T05:00:00.000Z"))
    inc_id = _incident_by_fp(fp1)
    _process(_payload(fp=fp2, starts_at="2026-09-06T05:30:00.000Z"))
    _process(_payload(fp=fp1, starts_at="2026-09-06T05:00:00.000Z", status="resolved",
                      ends_at="2026-09-06T06:00:00.000Z"))
    _process(_payload(fp=fp2, starts_at="2026-09-06T05:30:00.000Z", status="resolved",
                      ends_at="2026-09-06T06:30:00.000Z"))

    ev = _events(inc_id)
    types = [t for _, t, _ in ev]
    assert types[0] == "incident.created_from_alert"
    assert ev[0][2]["alert_name"] == "OrderOperationP95High"
    assert ev[0][2]["service"] == "order-service"
    assert ev[0][2]["operation"] == "ORDER_CREATE"
    assert ev[0][2]["occurrence_count"] == 1
    assert "incident.alert_merged" in types
    assert "incident.alert_resolved" in types
    resolved_payload = next(p for _, t, p in ev if t == "incident.alert_resolved")
    assert resolved_payload["recovered"] is False          # resolved ≠ 恢复
    assert "alert.received" not in types                   # D4:不发
    seqs = [s for s, _, _ in ev]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)  # 单调不重复
    _purge_demo_group()


def test_run_auto_started_emitted_once(monkeypatch, tmp_path):
    """run.auto_started 在封存+调度成功后恰一次(trigger_source=alertmanager)。"""
    from tests.test_v21c_baseline import _ok_capture
    _purge_demo_group()
    fp = uuid.uuid4().hex[:12]
    _process(_payload(fp=fp))
    inc_id = _incident_by_fp(fp)

    monkeypatch.setattr(runner, "_saver", None)
    monkeypatch.setattr(runner.settings, "checkpoint_path", str(tmp_path / "cp.db"))
    monkeypatch.setattr(baseline_capture, "capture_run_baselines",
                        lambda run, alert_starts_at=None: _ok_capture())
    started = []

    async def recording_start(incident_id, run_id_, thread_id):
        started.append(run_id_)

    monkeypatch.setattr(runner, "start_investigation", recording_start)
    import asyncio
    asyncio.run(dispatcher.dispatch_once())
    assert started == [_queued_run(inc_id).id]

    ev = [t for _, t, _ in _events(inc_id)]
    assert ev.count("run.auto_started") == 1
    payload = next(p for _, t, p in _events(inc_id) if t == "run.auto_started")
    assert payload["trigger_source"] == "alertmanager"
    assert payload["run_id"] == started[0]
    _purge_demo_group()


# ---------- 并发:resolved 与 Run 终态交错 ----------

def test_concurrent_resolved_and_terminal_converges_closed():
    """真实线程:resolved 交付与 Run 终态交错 → 无死锁,批后补偿收敛 CLOSED
    (锁序:网关 alert_instance→incident_alert→incident;T4 incident_alert→incident)。"""
    _purge_demo_group()
    fp = uuid.uuid4().hex[:12]
    _process(_payload(fp=fp))
    inc_id = _incident_by_fp(fp)
    run = _queued_run(inc_id)
    errors = []

    def do_resolved():
        try:
            _process(_payload(fp=fp, status="resolved",
                              ends_at="2026-09-06T06:00:00.000Z"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def do_terminal():
        try:
            run_repo.update_run_status(run.id, "failed")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=do_resolved)
    t2 = threading.Thread(target=do_terminal)
    t1.start(); t2.start(); t1.join(30); t2.join(30)
    assert errors == []                                    # 无死锁/无异常
    # 批后补偿(与 process_alert_batch 相同的收敛步骤)
    close_incident_if_resolved(inc_id)
    row = _incident_row(inc_id)
    assert row.alert_status == "RESOLVED"
    assert row.lifecycle_status == "CLOSED"                # 收敛关闭
    assert row.open_group_key is None
    _purge_demo_group()


# ---------- API 暴露 ----------

def test_api_exposes_alert_lifecycle_fields(monkeypatch, tmp_path):
    """/api/incidents 暴露 source/alert_status/lifecycle_status/occurrence_count/
    baseline_quality/auto_run_started_at(坐席可见)。"""
    from tests.test_v21c_baseline import _ok_capture
    _purge_demo_group()
    fp = uuid.uuid4().hex[:12]
    _process(_payload(fp=fp))
    inc_id = _incident_by_fp(fp)
    monkeypatch.setattr(runner, "_saver", None)
    monkeypatch.setattr(runner.settings, "checkpoint_path", str(tmp_path / "cp.db"))
    monkeypatch.setattr(baseline_capture, "capture_run_baselines",
                        lambda run, alert_starts_at=None: _ok_capture())

    async def recording_start(incident_id, run_id_, thread_id):
        return None

    monkeypatch.setattr(runner, "start_investigation", recording_start)
    import asyncio
    asyncio.run(dispatcher.dispatch_once())

    from app.main import app as fastapi_app
    client = TestClient(fastapi_app)                       # 不进 lifespan
    lst = client.get("/api/incidents").json()
    mine = next(i for i in lst if i["id"] == inc_id)
    assert mine["source"] == "alertmanager"
    assert mine["alert_status"] in ("FIRING", "RESOLVED")
    assert mine["lifecycle_status"] == "OPEN"
    assert mine["occurrence_count"] == 1
    detail = client.get(f"/api/incidents/{inc_id}").json()
    assert detail["baseline_quality"] == "OK"
    assert detail["auto_run_started_at"] is not None
    _purge_demo_group()
