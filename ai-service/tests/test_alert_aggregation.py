"""V2.1-B:聚合事务 + incident_alert + open_group_key 裁决(特征测试先行)。

复核修正覆盖:
- 聚合原子事务:事件/投影/Incident/关联/queued Run 同一 Session,故障注入回滚无孤儿;
- occurrence 矩阵:重复重放=1;不同 delivery 同 group=+1;archived/duplicate 不计;
- resolved 汇总按实例全集:任一 FIRING → Incident 仍 FIRING;全部非 FIRING → RESOLVED;
- 关联权威 = incident_alert(实例键唯一);dispatch_status 库默认 DISPATCHED;
- 服务端映射:alertname/service/operation 组合校验 + environment 白名单 + severity 映射;
- group_key = canonical_json 结构化哈希。
"""
import uuid
from datetime import datetime, timezone

from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun, Incident, IncidentAlert
from app.incident_gateway import service as gateway_service
from app.incident_gateway.schemas import AlertmanagerWebhookIn
from app.incident_gateway.registry import group_key_hash


def _purge_demo_group():
    """清除共享 demo group 的历史聚合数据(测试库为共享库,保证断言从零开始)。"""
    gk = group_key_hash("alertmanager", "OrderOperationP95High", "demo",
                        "order-service", "ORDER_CREATE")
    with Session(get_control_engine()) as s:
        s.execute(text(
            "DELETE agent_run FROM agent_run JOIN incident ON "
            "agent_run.incident_id = incident.id WHERE incident.group_key = :g"),
            {"g": gk})
        s.execute(text(
            "DELETE incident_alert FROM incident_alert JOIN incident ON "
            "incident_alert.incident_id = incident.id WHERE incident.group_key = :g"),
            {"g": gk})
        s.execute(text("DELETE FROM incident WHERE group_key = :g"), {"g": gk})
        s.commit()
from app.incident_gateway.fingerprint import gateway_lock_name


def _payload(*, fp=None, starts_at="2026-09-06T05:00:00.000Z", status="firing",
             labels=None, ends_at=None, alertname="OrderOperationP95High",
             service="order-service", operation="ORDER_CREATE", environment="demo"):
    return {
        "version": "4", "groupKey": "g", "status": status, "receiver": "tracemind",
        "alerts": [{
            "status": status,
            "labels": {"alertname": alertname, "service": service,
                       "operation": operation, "environment": environment},
            "annotations": {"summary": "t"},
            "startsAt": starts_at, "endsAt": ends_at,
            "fingerprint": fp or uuid.uuid4().hex[:12],
        }],
    }


def _process(payload):
    return gateway_service.process_alert_batch("alertmanager",
                                               AlertmanagerWebhookIn.model_validate(payload))


def _incident_for(fingerprint):
    with Session(get_control_engine()) as s:
        row = s.execute(text(
            "SELECT i.id, i.status, i.alert_status, i.lifecycle_status, i.occurrence_count, "
            "i.group_key, i.open_group_key, i.source, i.alert_name, i.environment, "
            "i.service_ref, i.affected_operation_ref, i.severity "
            "FROM incident i JOIN incident_alert ia ON ia.incident_id = i.id "
            "JOIN alert_instance ai ON ai.alert_instance_key = ia.alert_instance_key "
            "WHERE ai.external_fingerprint = :f"), {"f": fingerprint}).fetchone()
        return row


def _runs_for(inc_id):
    with Session(get_control_engine()) as s:
        rows = s.scalars(select(AgentRun).where(
            AgentRun.incident_id == inc_id).order_by(AgentRun.id)).all()
        return [SimpleNamespace(id=r.id, status=r.status, trigger_source=r.trigger_source,
                                dispatch_status=r.dispatch_status,
                                active_run_key=r.active_run_key,
                                incident_digest_baseline=r.incident_digest_baseline)
                for r in rows]


def _cleanup_group(fp):
    with Session(get_control_engine()) as s:
        s.execute(text(
            "DELETE agent_run FROM agent_run JOIN incident ON agent_run.incident_id=incident.id "
            "WHERE incident.id IN (SELECT incident_id FROM incident_alert ia "
            "JOIN alert_instance ai ON ai.alert_instance_key=ia.alert_instance_key "
            "WHERE ai.external_fingerprint=:f)"), {"f": fp})
        s.execute(text(
            "DELETE incident FROM incident WHERE id IN (SELECT incident_id FROM incident_alert ia "
            "JOIN alert_instance ai ON ai.alert_instance_key=ia.alert_instance_key "
            "WHERE ai.external_fingerprint=:f)"), {"f": fp})
        s.execute(text("DELETE FROM incident_alert WHERE alert_instance_key IN "
                       "(SELECT alert_instance_key FROM alert_instance "
                       "WHERE external_fingerprint=:f)"), {"f": fp})
        s.execute(text("DELETE FROM alert_instance WHERE external_fingerprint=:f"),
                  {"f": fp})
        s.execute(text("DELETE FROM alert_event WHERE external_fingerprint=:f"),
                  {"f": fp})
        s.commit()


# ---------- 基本聚合 ----------

def test_first_firing_creates_incident_and_queued_run():
    _purge_demo_group()
    fp = uuid.uuid4().hex[:12]
    out = _process(_payload(fp=fp))
    inc = _incident_for(fp)
    assert inc is not None
    assert inc.alert_status == "FIRING" and inc.lifecycle_status == "OPEN"
    assert inc.status == "created" and inc.occurrence_count == 1
    assert inc.source == "alertmanager" and inc.alert_name == "OrderOperationP95High"
    assert inc.environment == "demo" and inc.service_ref == "order-service"
    assert inc.affected_operation_ref == "ORDER_CREATE" and inc.severity == "high"
    assert inc.open_group_key == inc.group_key and len(inc.group_key) == 64
    runs = _runs_for(inc.id)
    assert len(runs) == 1
    assert runs[0].status == "queued" and runs[0].trigger_source == "alertmanager"
    assert runs[0].dispatch_status == "READY"
    assert runs[0].active_run_key == f"incident:{inc.id}"
    assert runs[0].incident_digest_baseline in (None, 'null')  # 基线语义:自动 Run 不采集
    assert out["created_incidents"] == [inc.id]
    _cleanup_group(fp)


def test_second_delivery_same_group_aggregates_occurrence():
    _purge_demo_group()
    fp1, fp2 = uuid.uuid4().hex[:12], uuid.uuid4().hex[:12]
    _process(_payload(fp=fp1, starts_at="2026-09-06T05:00:00.000Z"))
    _process(_payload(fp=fp2, starts_at="2026-09-06T05:30:00.000Z"))   # 同 group 不同实例
    inc = _incident_for(fp2)
    assert inc is not None and inc.occurrence_count == 2
    runs = _runs_for(inc.id)
    assert len(runs) == 1                                   # 不自动重跑
    _cleanup_group(fp1); _cleanup_group(fp2)


def test_exact_replay_does_not_touch_occurrence():
    _purge_demo_group()
    fp = uuid.uuid4().hex[:12]
    payload = _payload(fp=fp)
    for _ in range(10):
        _process(payload)
    inc = _incident_for(fp)
    assert inc.occurrence_count == 1
    with Session(get_control_engine()) as s:
        n_inst = s.execute(text("SELECT COUNT(*) FROM alert_instance "
                                "WHERE external_fingerprint=:f"), {"f": fp}).scalar()
        n_ev = s.execute(text("SELECT COUNT(*) FROM alert_event "
                              "WHERE external_fingerprint=:f"), {"f": fp}).scalar()
    assert n_inst == 1 and n_ev == 1
    _cleanup_group(fp)


def test_archived_late_firing_does_not_touch_occurrence():
    """同 fingerprint:11:00 FIRING 在先;未落库的 09:00 FIRING(更早)→ 只归档事件,
    不建第二个实例、不计 occurrence(复核修正 ①)。"""
    _purge_demo_group()
    fp = uuid.uuid4().hex[:12]
    _process(_payload(fp=fp, starts_at="2026-09-06T11:00:00.000Z"))
    out = _process(_payload(fp=fp, starts_at="2026-09-06T09:00:00.000Z"))  # 迟到更早
    inc = _incident_for(fp)
    assert inc.occurrence_count == 1                        # archived 不计
    with Session(get_control_engine()) as s:
        n_inst = s.execute(text("SELECT COUNT(*) FROM alert_instance "
                                "WHERE external_fingerprint=:f"), {"f": fp}).scalar()
        n_ev = s.execute(text("SELECT COUNT(*) FROM alert_event "
                              "WHERE external_fingerprint=:f"), {"f": fp}).scalar()
    assert n_inst == 1 and n_ev == 2 and out["events"]["archived_late_firing"] == 1
    _cleanup_group(fp)


# ---------- resolved 汇总按实例全集 ----------

def test_resolved_summary_requires_all_instances_resolved():
    _purge_demo_group()
    fp1, fp2 = uuid.uuid4().hex[:12], uuid.uuid4().hex[:12]
    _process(_payload(fp=fp1, starts_at="2026-09-06T05:00:00.000Z"))
    _process(_payload(fp=fp2, starts_at="2026-09-06T05:30:00.000Z"))
    _process(_payload(fp=fp1, starts_at="2026-09-06T05:00:00.000Z", status="resolved",
                      ends_at="2026-09-06T05:10:00.000Z"))
    assert _incident_for(fp1).alert_status == "FIRING"       # fp2 仍 FIRING
    _process(_payload(fp=fp2, starts_at="2026-09-06T05:30:00.000Z", status="resolved",
                      ends_at="2026-09-06T05:40:00.000Z"))
    assert _incident_for(fp1).alert_status == "RESOLVED"     # 全部 resolved
    assert _incident_for(fp1).lifecycle_status == "OPEN"     # C 才关闭
    _cleanup_group(fp1); _cleanup_group(fp2)


# ---------- 服务端映射与聚合键 ----------

def test_label_mismatch_with_registry_is_ignored():
    """服务端映射不一致:事件+实例照常归档(单向状态机),但不聚合、不建 Run,
    计 ignored + reason=v3 计划 §四 唯一语义。"""
    fp = uuid.uuid4().hex[:12]
    out = _process(_payload(fp=fp, service="inventory-service"))   # 与规则不符
    assert out["ignored"] == 1
    assert _incident_for(fp) is None                               # 不聚合
    with Session(get_control_engine()) as s:
        n_ev = s.execute(text("SELECT COUNT(*) FROM alert_event WHERE external_fingerprint=:f"),
                         {"f": fp}).scalar()
        st = s.execute(text("SELECT current_status FROM alert_instance WHERE external_fingerprint=:f"),
                       {"f": fp}).scalar()
    assert n_ev == 1 and st == "FIRING"                            # 归档+投影照常
    _cleanup_group(fp)


def test_unknown_environment_is_ignored():
    fp = uuid.uuid4().hex[:12]
    out = _process(_payload(fp=fp, environment="prod"))            # 环境白名单外
    assert out["ignored"] == 1 and _incident_for(fp) is None       # 不聚合
    with Session(get_control_engine()) as s:
        n_ev = s.execute(text("SELECT COUNT(*) FROM alert_event WHERE external_fingerprint=:f"),
                         {"f": fp}).scalar()
    assert n_ev == 1                                               # 归档照常
    _cleanup_group(fp)


def test_group_key_is_structured_hash():
    """group_key 为 canonical_json 结构化哈希:键序不敏感、不同元组不同键。"""
    _purge_demo_group()
    import hashlib
    from app.incident_gateway.fingerprint import canonical_json
    fp1, fp2 = uuid.uuid4().hex[:12], uuid.uuid4().hex[:12]
    _process(_payload(fp=fp1, starts_at="2026-09-06T05:00:00.000Z"))
    _process(_payload(fp=fp2, starts_at="2026-09-06T06:00:00.000Z",
                      alertname="OrderOperationP95High", service="order-service",
                      operation="ORDER_CREATE", environment="demo"))
    inc = _incident_for(fp2)
    expected = hashlib.sha256(canonical_json({
        "source": "alertmanager", "alertname": "OrderOperationP95High",
        "environment": "demo", "service": "order-service",
        "operation": "ORDER_CREATE"}).encode("utf-8")).hexdigest()
    assert inc.group_key == expected
    _cleanup_group(fp1); _cleanup_group(fp2)


# ---------- 原子事务:故障注入 ----------

def _fp_batch(fp, starts_at="2026-09-06T05:00:00.000Z"):
    return AlertmanagerWebhookIn.model_validate(_payload(fp=fp, starts_at=starts_at))


def test_fault_injection_rollback_converges(monkeypatch):
    """分别在 投影/Incident/关联/Run 处抛异常:回滚无孤儿;修复后重试收敛。"""
    _purge_demo_group()
    fp = uuid.uuid4().hex[:12]
    payload = _fp_batch(fp)

    import app.incident_gateway.service as gs

    # 1) 投影处抛异常(alert_instance upsert)
    def boom_upsert(*a, **kw):
        raise RuntimeError("inject-instance")
    monkeypatch.setattr(gs, "upsert_instance", boom_upsert)
    with pytest.raises(RuntimeError):
        gs.process_alert_batch("alertmanager", payload)
    monkeypatch.undo()

    def boom_incident(*a, **kw):
        raise RuntimeError("inject-incident")
    monkeypatch.setattr(gs, "_insert_incident", boom_incident)
    with pytest.raises(RuntimeError):
        gs.process_alert_batch("alertmanager", payload)
    monkeypatch.undo()

    def boom_link(*a, **kw):
        raise RuntimeError("inject-link")
    monkeypatch.setattr(gs, "_link_incident_alert", boom_link)
    with pytest.raises(RuntimeError):
        gs.process_alert_batch("alertmanager", payload)
    monkeypatch.undo()

    def boom_run(*a, **kw):
        raise RuntimeError("inject-run")
    monkeypatch.setattr(gs, "_insert_queued_run", boom_run)
    with pytest.raises(RuntimeError):
        gs.process_alert_batch("alertmanager", payload)
    monkeypatch.undo()

    # 回滚后无孤儿(事件也无 —— 同事务)
    with Session(get_control_engine()) as s:
        n_ev = s.execute(text("SELECT COUNT(*) FROM alert_event "
                              "WHERE external_fingerprint=:f"), {"f": fp}).scalar()
        n_inst = s.execute(text("SELECT COUNT(*) FROM alert_instance "
                                "WHERE external_fingerprint=:f"), {"f": fp}).scalar()
    assert n_ev == 0 and n_inst == 0

    # 修复后重试收敛
    out = _process(payload)
    assert out["created_incidents"]
    assert _incident_for(fp) is not None
    assert len(_runs_for(out["created_incidents"][0])) == 1
    _cleanup_group(fp)


def test_fault_injection_event_rollback_no_orphan_event(monkeypatch):
    """事件插入后于 Incident 处抛异常:alert_event 一并回滚(同一事务)。"""
    _purge_demo_group()
    fp = uuid.uuid4().hex[:12]
    import app.incident_gateway.service as gs
    monkeypatch.setattr(gs, "_insert_incident", lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("inject")))
    with pytest.raises(RuntimeError):
        gs.process_alert_batch("alertmanager", _fp_batch(fp))
    with Session(get_control_engine()) as s:
        n_ev = s.execute(text("SELECT COUNT(*) FROM alert_event "
                              "WHERE external_fingerprint=:f"), {"f": fp}).scalar()
    assert n_ev == 0
