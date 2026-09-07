"""V2.1-A:告警网关服务 —— AlertEvent 落库 + AlertInstance 单向投影。

处理流程(逐条 alert):
1. 边界校验:alertname 白名单、标签白名单/长度 → 违规 ignored(不落库);
2. delivery_hash 幂等:INSERT alert_event,唯一键 (source, delivery_hash) 冲突
   → 计 duplicate 并跳过(精确重放不重复计数、不重复投影);
3. AlertInstance 投影(单向状态机):
   - 无实例:FIRING → 建 FIRING(v1);RESOLVED → 建 RESOLVED(resolved-only 归档语义);
   - 有 FIRING 实例:RESOLVED → CAS 推进(version+1, resolved_at);
     FIRING → 仅更新 last_received_at/last_event_id;
   - 有 RESOLVED 实例:RESOLVED → 更新 last_received_at;FIRING → 迟到,只归档不重开。

V2.1-A 不创建 Incident、不启动 Run(响应 created/updated 恒为空;聚合与调度属 V2.1-B)。
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db.engine import get_control_engine
from app.db.models import AgentRun, Incident, IncidentAlert
from sqlalchemy.orm import Session
from app.incident_gateway.fingerprint import (alert_instance_key, canonical_json,
                                              delivery_hash, gateway_lock_name,
                                              normalize_labels)
from app.tools_core.errors import ToolBusinessError
from app.tools_core.errors import ToolBusinessError as _TBE
from app.incident_gateway.registry import group_key_hash, resolve_alert
from app.incident_gateway.schemas import AlertmanagerWebhookIn

logger = logging.getLogger(__name__)


def _to_naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt                      # 视为 UTC(naive)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _insert_event(conn, *, source: str, fingerprint: str, instance_key: str,
                  delivery: str, status: str, starts_at, ends_at, received_at,
                  labels_json: str, annotations_json: str, payload_json: str) -> int | None:
    """插入 AlertEvent;唯一键冲突(精确重放)返回 None。"""
    from sqlalchemy.exc import IntegrityError
    try:
        return _insert_event_once(
            conn, source=source, fingerprint=fingerprint, instance_key=instance_key,
            delivery=delivery, status=status, starts_at=starts_at, ends_at=ends_at,
            received_at=received_at, labels_json=labels_json,
            annotations_json=annotations_json, payload_json=payload_json)
    except IntegrityError:
        return None


def _insert_event_once(conn, *, source: str, fingerprint: str, instance_key: str,
                       delivery: str, status: str, starts_at, ends_at, received_at,
                       labels_json: str, annotations_json: str, payload_json: str) -> int:
    res = conn.execute(text(
        "INSERT INTO alert_event (source, external_fingerprint, alert_instance_key, "
        "delivery_hash, alert_status, starts_at, ends_at, received_at, "
        "labels_json, annotations_json, payload_json) "
        "VALUES (:source, :fp, :ik, :dh, :st, :sa, :ea, :ra, :lj, :aj, :pj)"),
        {"source": source, "fp": fingerprint, "ik": instance_key, "dh": delivery,
         "st": status, "sa": starts_at, "ea": ends_at, "ra": received_at,
         "lj": labels_json, "aj": annotations_json, "pj": payload_json})
    return int(res.lastrowid)


def upsert_instance(conn, *, instance_key: str, source: str, fingerprint: str,
                     starts_at, received_at, event_id: int, status: str,
                     ends_at) -> str:
    """AlertInstance 单向投影。

    V2.1-A closure:
    - 由调用方持有的 source+fingerprint advisory lock 串行化(跨事务直到提交后释放);
    - 仅当 startsAt 晚于该 fingerprint 已有实例的最新 starts_at 时才创建新 FIRING
      实例;未见过但更早的 FIRING 只归档事件(archived_late_firing),不建实例;
    - resolved-only 建实例、FIRING→RESOLVED 单向 CAS 语义不变;
    - RESOLVED 实例之后更晚 startsAt 的 FIRING 视为新 episode,可创建新实例。
    返回动作:created_firing/created_resolved/advanced_resolved/
    touched_firing/touched_resolved/archived_late_firing。
    """
    from sqlalchemy.exc import IntegrityError

    current = conn.execute(text(
        "SELECT current_status FROM alert_instance WHERE alert_instance_key=:ik "
        "FOR UPDATE"), {"ik": instance_key}).scalar()
    if current is None and status == "firing":
        # 同 fingerprint 的最新 starts_at 约束:不晚于已有实例 → 只归档
        latest = conn.execute(text(
            "SELECT MAX(starts_at) FROM alert_instance "
            "WHERE source=:s AND external_fingerprint=:fp"),
            {"s": source, "fp": fingerprint}).scalar()
        if latest is not None and starts_at <= latest:
            return "archived_late_firing"
    if current is None:
        try:
            conn.execute(text(
                "INSERT INTO alert_instance (alert_instance_key, source, "
                "external_fingerprint, starts_at, current_status, resolved_at, "
                "last_received_at, last_event_id, version) "
                "VALUES (:ik, :source, :fp, :sa, :st, :ra_end, :ra, :eid, 1)"),
                {"ik": instance_key, "source": source, "fp": fingerprint,
                 "sa": starts_at, "st": status.upper(),
                 "ra_end": (ends_at or received_at) if status == "resolved" else None,
                 "ra": received_at, "eid": event_id})
            return f"created_{status}"
        except IntegrityError:
            current = conn.execute(text(
                "SELECT current_status FROM alert_instance WHERE alert_instance_key=:ik "
                "FOR UPDATE"), {"ik": instance_key}).scalar()
            if current is None:
                raise
    if current == "FIRING":
        if status == "firing":
            conn.execute(text(
                "UPDATE alert_instance SET last_received_at=:ra, last_event_id=:eid "
                "WHERE alert_instance_key=:ik"),
                {"ra": received_at, "eid": event_id, "ik": instance_key})
            return "touched_firing"
        conn.execute(text(
            "UPDATE alert_instance SET current_status='RESOLVED', resolved_at=:ea, "
            "last_received_at=:ra, last_event_id=:eid, version=version+1 "
            "WHERE alert_instance_key=:ik AND current_status='FIRING'"),
            {"ea": ends_at or received_at, "ra": received_at, "eid": event_id,
             "ik": instance_key})
        return "advanced_resolved"
    # current == RESOLVED
    if status == "resolved":
        conn.execute(text(
            "UPDATE alert_instance SET last_received_at=:ra, last_event_id=:eid "
            "WHERE alert_instance_key=:ik"),
            {"ra": received_at, "eid": event_id, "ik": instance_key})
        return "touched_resolved"
    # 迟到 FIRING:更晚 startsAt → 新 episode;否则只归档
    latest = conn.execute(text(
        "SELECT MAX(starts_at) FROM alert_instance "
        "WHERE source=:s AND external_fingerprint=:fp"),
        {"s": source, "fp": fingerprint}).scalar()
    if starts_at <= latest:
        return "archived_late_firing"
    conn.execute(text(
        "INSERT INTO alert_instance (alert_instance_key, source, "
        "external_fingerprint, starts_at, current_status, resolved_at, "
        "last_received_at, last_event_id, version) "
        "VALUES (:ik, :source, :fp, :sa, 'FIRING', NULL, :ra, :eid, 1)"),
        {"ik": instance_key, "source": source, "fp": fingerprint,
         "sa": starts_at, "ra": received_at, "eid": event_id})
    return "created_firing"


def settings_allowlist() -> str:
    from app.config import settings
    return settings.alertmanager_alertname_allowlist


MAX_TX_RETRIES = 3


def _insert_incident(session, resolved, group_key, received_at, labels, annotations):
    """新建聚合 Incident(OPEN/FIRING/occurrence=1)。故障注入点。"""
    incident = Incident(
        title=f"[{resolved.environment}] {resolved.alertname} on {resolved.service}",
        severity=resolved.severity, service_ref=resolved.service,
        affected_operation_ref=resolved.operation, status="created",
        source="alertmanager", alert_name=resolved.alertname,
        environment=resolved.environment, alert_status="FIRING",
        lifecycle_status="OPEN", group_key=group_key, open_group_key=group_key,
        first_seen_at=received_at, last_seen_at=received_at, occurrence_count=1,
        labels_json=labels, annotations_json=annotations)
    session.add(incident)
    session.flush()
    return incident


def _link_incident_alert(session, incident_id, instance_key):
    """权威关联(实例键唯一;重复关联由唯一键约束兜底)。故障注入点。"""
    session.add(IncidentAlert(incident_id=incident_id,
                              alert_instance_key=instance_key))
    session.flush()


def _insert_queued_run(session, incident):
    """新建 Incident 的首个 queued Run(alertmanager,基线 None)。故障注入点。"""
    from app.repositories.run_repo import _insert_run_in_session

    return _insert_run_in_session(
        session, incident, baseline=None, trigger_source="alertmanager",
        status="queued", active_run_key=f"incident:{incident.id}")


def _linked_incident_id(session, instance_key):
    """权威关联查询(incident_alert)。"""
    return session.scalars(text(
        "SELECT incident_id FROM incident_alert WHERE alert_instance_key=:ik "
        "FOR UPDATE"), {"ik": instance_key}).first()


def _firing_instances_remaining(session, incident_id):
    """resolved 汇总:任一关联实例仍 FIRING → Incident 保持 FIRING(复核修正 1)。"""
    return session.execute(text(
        "SELECT COUNT(*) FROM incident_alert ia "
        "JOIN alert_instance ai ON ai.alert_instance_key = ia.alert_instance_key "
        "WHERE ia.incident_id = :lid AND ai.current_status = 'FIRING'"),
        {"lid": incident_id}).scalar() or 0


def _is_group_conflict_or_deadlock(exc):
    """open_group_key 唯一冲突或死锁(整事务重试;其余异常不重试)。"""
    msg = str(exc).lower()
    return "uk_incident_open_group" in msg or "deadlock" in msg or "1213" in msg


def _process_one(source, alert, received_at, allowlist):
    """单条告警:事件 + 投影 + 聚合 + 关联 + queued Run(同一 Session 事务;
    open_group_key 冲突/死锁 → 整事务回滚 + 有上限重试,失败 Session 不复用)。"""
    labels = dict(alert.labels)
    alertname = labels.get("alertname")
    if alertname not in allowlist:
        return {"received": 1, "ignored": 1, "reason": "alertname_not_allowed"}
    kept, violations = normalize_labels(labels)
    if violations or "alertname" not in kept:
        return {"received": 1, "ignored": 1, "reason": "label_bounds"}
    resolved = resolve_alert(kept)   # 服务端映射;None → 归档+投影照常但不聚合(V2.1-B §四)
    starts_at = _to_naive_utc(alert.startsAt)
    ends_at = _to_naive_utc(alert.endsAt)
    starts_iso = starts_at.isoformat()   # 已归一化 UTC(Z/+00:00/其他时区同刻同实例)
    instance_key = alert_instance_key(source, alert.fingerprint, starts_iso)
    normalized = {"status": alert.status, "labels": kept,
                  "annotations": dict(alert.annotations), "startsAt": starts_iso,
                  "endsAt": ends_at.isoformat() if ends_at else None,
                  "fingerprint": alert.fingerprint}
    delivery = delivery_hash(normalized)

    # advisory lock(独立连接)按 source+fingerprint 串行化,跨事务持有直到提交后释放
    # (同 group 不同 fingerprint 由 open_group_key 唯一裁决 + 整事务重试收敛)
    lock_conn = get_control_engine().connect()
    lock_name = gateway_lock_name(source, alert.fingerprint)
    got = lock_conn.execute(text("SELECT GET_LOCK(:l, 5)"), {"l": lock_name}).scalar()
    if got != 1:
        lock_conn.close()
        raise ToolBusinessError("GATEWAY_LOCK_TIMEOUT",
                                f"获取告警实例锁超时: {lock_name}", retryable=True)
    last_exc = None
    try:
        for _attempt in range(1, MAX_TX_RETRIES + 1):
            session = Session(get_control_engine())
            try:
                with session.begin():
                    event_id = _insert_event(
                        session, source=source, fingerprint=alert.fingerprint,
                        instance_key=instance_key, delivery=delivery,
                        status=alert.status, starts_at=starts_at, ends_at=ends_at,
                        received_at=received_at, labels_json=canonical_json(kept),
                        annotations_json=canonical_json(dict(alert.annotations)),
                        payload_json=canonical_json(normalized))
                    if event_id is None:
                        return {"received": 1, "events": {"duplicates": 1}}
                    action = upsert_instance(
                        session, instance_key=instance_key, source=source,
                        fingerprint=alert.fingerprint, starts_at=starts_at,
                        received_at=received_at, event_id=event_id,
                        status=alert.status, ends_at=ends_at)
                    if action == "archived_late_firing":
                        return {"received": 1, "events": {"archived_late_firing": 1}}
                    out = {"received": 1, "events": {"appended": 1}}
                    if resolved is None:
                        # 服务端映射失败:事件+实例照常归档/投影,但不聚合、不建
                        # Run、不计 occurrence(复核意见 6 的唯一语义)
                        out["ignored"] = 1
                        out["reason"] = "incomplete_labels"
                    linked_id = _linked_incident_id(session, instance_key)
                    incident = None
                    is_new_incident = False
                    if action in ("created_firing", "touched_firing") and \
                            resolved is not None:
                        group_key = group_key_hash(
                            source, resolved.alertname, resolved.environment,
                            resolved.service, resolved.operation)
                        if linked_id is not None:
                            incident = session.get(Incident, linked_id)
                            incident.occurrence_count += 1
                            incident.last_seen_at = received_at
                            incident.alert_status = "FIRING"
                        else:
                            incident = session.scalars(
                                select(Incident).where(
                                    Incident.open_group_key == group_key
                                ).with_for_update()).first()
                            if incident is None:
                                incident = _insert_incident(
                                    session, resolved, group_key, received_at,
                                    kept, dict(alert.annotations))
                                is_new_incident = True
                            else:
                                incident.occurrence_count += 1
                                incident.last_seen_at = received_at
                                incident.alert_status = "FIRING"
                        _link_incident_alert(session, incident.id, instance_key)
                    if action in ("advanced_resolved", "touched_resolved"):
                        if linked_id is None:
                            linked_id = _linked_incident_id(session, instance_key)
                        if linked_id is not None:
                            incident = session.get(Incident, linked_id)
                            # resolved 汇总按实例全集:任一关联实例仍 FIRING →
                            # 保持 FIRING;全部非 FIRING → RESOLVED
                            # (不关 lifecycle、不清 open_group_key、无 SELF_RECOVERED)
                            if _firing_instances_remaining(session, linked_id) == 0:
                                incident.alert_status = "RESOLVED"
                    if (is_new_incident and resolved is not None
                            and action == "created_firing"):
                        _insert_queued_run(session, incident)
                    if incident is not None:
                        if is_new_incident:
                            out["created_incidents"] = [incident.id]
                        else:
                            out["updated_incidents"] = [incident.id]
                    return out
            except (IntegrityError, OperationalError) as exc:
                last_exc = exc
                session.close()   # 失败 Session 不复用(禁止在失败 Session 中继续查询)
                if not _is_group_conflict_or_deadlock(exc):
                    raise
        raise ToolBusinessError(
            "AGGREGATION_RETRY_EXHAUSTED",
            f"聚合事务重试 {MAX_TX_RETRIES} 次仍冲突: {last_exc}", retryable=True)
    finally:
        try:
            lock_conn.execute(text("SELECT RELEASE_LOCK(:l)"), {"l": lock_name})
        except Exception:  # noqa: BLE001 释放失败不覆盖业务异常、不阻止关连接
            logger.warning("RELEASE_LOCK 失败(连接将关闭): %s", lock_name)
        finally:
            lock_conn.close()


def settings_allowlist():
    from app.config import settings
    return settings.alertmanager_alertname_allowlist


def process_alert_batch(source, payload):
    """处理一批 Alertmanager 告警;返回方案约定的响应结构 + 事件明细计数。
    V2.1-B:事件/投影/聚合/关联/queued Run 同一事务(Webhook 仅快速持久化,
    Agent 由 Dispatcher 启动)。"""
    allowlist = {a.strip() for a in settings_allowlist().split(",") if a.strip()}
    received_at = datetime.now(timezone.utc).replace(tzinfo=None)
    counters = {"received": len(payload.alerts), "ignored": 0,
                "created_incidents": [], "updated_incidents": [], "reasons": [],
                "events": {"appended": 0, "duplicates": 0,
                           "archived_late_firing": 0}}
    for alert in payload.alerts:
        out = _process_one(source, alert, received_at, allowlist)
        if out.get("ignored"):
            counters["ignored"] += 1
            if out.get("reason"):
                counters["reasons"].append(out["reason"])
        for key in ("created_incidents", "updated_incidents"):
            counters[key].extend(out.get(key) or [])
        for k, v in (out.get("events") or {}).items():
            counters["events"][k] = counters["events"].get(k, 0) + v
    return counters
