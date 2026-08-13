"""lock_observation:blocker_ref → 阻塞关系身份(持久化,不依赖 MCP Server 内存;V1.3 §3.2)。"""
from sqlalchemy import text
from app.db.engine import get_control_engine

# 惰性获取:函数内调用 get_control_engine()(offline_eval 下模块导入不触 DB)


def upsert(*, incident_id: int, agent_run_id: int, blocker_ref: str,
           transaction_id, processlist_id, blocking_lock_ref, relation_identity_hash,
           observed_at, expires_at) -> None:
    control_engine = get_control_engine()
    with control_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO lock_observation (incident_id, agent_run_id, blocker_ref, "
            "transaction_id, processlist_id, blocking_lock_ref, relation_identity_hash, "
            "observed_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON DUPLICATE KEY UPDATE processlist_id=VALUES(processlist_id), "
            "relation_identity_hash=VALUES(relation_identity_hash), "
            "expires_at=VALUES(expires_at)"),
            (incident_id, agent_run_id, blocker_ref, transaction_id, processlist_id,
             blocking_lock_ref, relation_identity_hash, observed_at, expires_at))


def get(incident_id: int, agent_run_id: int, blocker_ref: str) -> dict | None:
    control_engine = get_control_engine()
    with control_engine.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM lock_observation WHERE incident_id=? AND agent_run_id=? "
            "AND blocker_ref=?"), (incident_id, agent_run_id, blocker_ref)).mappings().first()
        return dict(row) if row else None
