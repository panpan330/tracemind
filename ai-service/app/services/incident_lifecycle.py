"""V2.1-C:episode 生命周期 —— Incident 关闭(告警闭环的收尾边界)。

关闭条件(**全部满足,缺一不可**,计划 v2 §九):
① `alert_status == 'RESOLVED'`(resolved 复核语义:resolved 本身不宣告恢复,
   但它是 episode 关闭的必要条件);
② 关联 AlertInstance **全部非 FIRING**(实例全集汇总,复用网关同款判定);
③ **不存在非终态 Run** —— 按 `status NOT IN TERMINAL_STATUSES` 判定
   (`active_run_key IS NULL` 仅作双保险;审批等待/调查中的 Run 一律阻止关闭);
④ `lifecycle_status` 仍为 `'OPEN'`(幂等:已 CLOSED → 跳过)。
满足 → `lifecycle_status='CLOSED'` + `open_group_key=NULL` + `closed_at=now`(同事务)。

调用点与事务边界(计划 v2 §四 T4):
- 网关 resolved 路径:T1 聚合事务内调用(`maybe_close_incident`,锁已按
  incident_alert → incident 持有);
- `run_repo.update_run_status` 终态**提交后**:独立短事务
  (`close_incident_if_resolved`,T4);
- `process_alert_batch` 批后补偿:Run 终态提交与 resolved 提交错开时收敛。

锁顺序与网关聚合一致:incident_alert → incident;agent_run 只读计数(不取行锁),
与 T2(incident → agent_run)无环。关闭后同组再次 FIRING → `open_group_key`
为 NULL → 网关创建**新 episode**(011 唯一键设计的用途)。
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine

logger = logging.getLogger(__name__)


def maybe_close_incident(session, incident_id: int) -> bool:
    """在调用方事务内判定并关闭 episode;返回是否发生关闭(幂等,可重复调用)。"""
    row = session.execute(text(
        "SELECT alert_status, lifecycle_status FROM incident "
        "WHERE id = :i FOR UPDATE"), {"i": incident_id}).fetchone()
    if row is None or row.alert_status != "RESOLVED" \
            or row.lifecycle_status != "OPEN":
        return False
    firing = session.execute(text(
        "SELECT COUNT(*) FROM incident_alert ia "
        "JOIN alert_instance ai ON ai.alert_instance_key = ia.alert_instance_key "
        "WHERE ia.incident_id = :i AND ai.current_status = 'FIRING'"),
        {"i": incident_id}).scalar() or 0
    if firing:
        return False
    from app.repositories import run_repo
    terminals = sorted(run_repo.TERMINAL_STATUSES)
    placeholders = ",".join(f":t{i}" for i in range(len(terminals)))
    params = {"i": incident_id}
    for i, t in enumerate(terminals):
        params[f"t{i}"] = t
    non_terminal = session.execute(text(
        f"SELECT COUNT(*) FROM agent_run WHERE incident_id = :i "
        f"AND status NOT IN ({placeholders})"), params).scalar() or 0
    if non_terminal:
        logger.info("episode %s 关闭暂缓:存在 %d 个非终态 Run", incident_id, non_terminal)
        return False
    session.execute(text(
        "UPDATE incident SET lifecycle_status = 'CLOSED', open_group_key = NULL, "
        "closed_at = :now WHERE id = :i"),
        {"now": datetime.now(timezone.utc).replace(tzinfo=None), "i": incident_id})
    logger.info("episode %s 已关闭(CLOSED,open_group_key 释放)", incident_id)
    return True


def close_incident_if_resolved(incident_id: int) -> bool:
    """独立短事务版(Run 终态提交后 / 批后补偿调用;与 T1 内调用共用条件判定)。"""
    with Session(get_control_engine()) as session:
        with session.begin():
            # 锁顺序与网关聚合一致:incident_alert → incident
            session.execute(text(
                "SELECT alert_instance_key FROM incident_alert "
                "WHERE incident_id = :i FOR UPDATE"), {"i": incident_id})
            session.execute(text(
                "SELECT id FROM incident WHERE id = :i FOR UPDATE"),
                {"i": incident_id})
            return maybe_close_incident(session, incident_id)
