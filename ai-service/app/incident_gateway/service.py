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

from sqlalchemy import text

from app.db.engine import get_control_engine
from app.incident_gateway.fingerprint import (alert_instance_key, canonical_json,
                                              delivery_hash, normalize_labels)
from app.tools_core.errors import ToolBusinessError
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


def _upsert_instance(conn, *, instance_key: str, source: str, fingerprint: str,
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


def process_alert_batch(source: str, payload: AlertmanagerWebhookIn) -> dict:
    """处理一批 Alertmanager 告警;返回方案约定的响应结构 + 事件明细计数。"""
    allowlist = {a.strip() for a in settings_allowlist().split(",") if a.strip()}
    received_at = datetime.now(timezone.utc).replace(tzinfo=None)
    counters = {"received": len(payload.alerts), "ignored": 0, "created_incidents": [],
                "updated_incidents": [], "events": {"appended": 0, "duplicates": 0,
                                                     "archived_late_firing": 0,
                                                     "projected": 0}}
    for alert in payload.alerts:
        labels = dict(alert.labels)
        alertname = labels.get("alertname")
        if alertname not in allowlist:
            counters["ignored"] += 1
            continue
        kept_labels, violations = normalize_labels(labels)
        if violations or "alertname" not in kept_labels:
            logger.warning("alert %s 边界违规被忽略: %s", alert.fingerprint, violations)
            counters["ignored"] += 1
            continue

        starts_at = _to_naive_utc(alert.startsAt)
        ends_at = _to_naive_utc(alert.endsAt)
        # V2.1-A closure:先统一规范化为 naive UTC,再生成 instance_key 与 delivery_hash
        # —— 同一时刻的 Z / +00:00 / 其他时区表达产生同一实例
        starts_iso = starts_at.isoformat()
        instance_key = alert_instance_key(source, alert.fingerprint, starts_iso)
        normalized = {"status": alert.status, "labels": kept_labels,
                      "annotations": dict(alert.annotations),
                      "startsAt": starts_iso,
                      "endsAt": ends_at.isoformat() if ends_at else None,
                      "fingerprint": alert.fingerprint}
        delivery = delivery_hash(normalized)

        # V2.1-A closure:advisory lock 用独立连接持有,跨越整个事务直到提交之后
        # (同连接持锁会在 finally 先于 commit 释放,导致并发事务看不到最新实例)
        lock_conn = get_control_engine().connect()
        lock_name = f"alertgw:{source}:{alert.fingerprint}"
        got = lock_conn.execute(text("SELECT GET_LOCK(:l, 5)"), {"l": lock_name}).scalar()
        if got != 1:
            lock_conn.close()
            raise ToolBusinessError("GATEWAY_LOCK_TIMEOUT",
                                    f"获取告警实例锁超时: {lock_name}", retryable=True)
        try:
            with get_control_engine().begin() as conn:
                event_id = _insert_event(
                    conn, source=source, fingerprint=alert.fingerprint,
                    instance_key=instance_key, delivery=delivery,
                    status=alert.status, starts_at=starts_at, ends_at=ends_at,
                    received_at=received_at,
                    labels_json=canonical_json(kept_labels),
                    annotations_json=canonical_json(dict(alert.annotations)),
                    payload_json=canonical_json(normalized))
                if event_id is None:
                    counters["events"]["duplicates"] += 1
                    continue          # 精确重放:事件与投影均不再推进
                counters["events"]["appended"] += 1
                action = _upsert_instance(
                    conn, instance_key=instance_key, source=source,
                    fingerprint=alert.fingerprint, starts_at=starts_at,
                    received_at=received_at, event_id=event_id,
                    status=alert.status, ends_at=ends_at)
        except Exception:
            logger.exception("alert %s 处理失败", alert.fingerprint)
            raise
        finally:
            lock_conn.execute(text("SELECT RELEASE_LOCK(:l)"), {"l": lock_name})
            lock_conn.close()
        if action.startswith("created") or action == "advanced_resolved":
            counters["events"]["projected"] += 1
        elif action == "archived_late_firing":
            counters["events"]["archived_late_firing"] += 1
    return counters


def settings_allowlist() -> str:
    from app.config import settings
    return settings.alertmanager_alertname_allowlist
