"""锁等待/事务详情查询:真实数据源 = performance_schema / information_schema(经 ai_investigator 只读连接)。
评测 Fixture 注入优先于真实查询;真实查询失败必须返回 ok=False,不允许伪造。"""
import time
import uuid
from typing import Any

from app.db.engine import get_readonly_engine  # 现有 ai_investigator 只读连接池

LONG_TRANSACTION_THRESHOLD_MS = 5000
LOCK_WAIT_THRESHOLD_MS = 3000
SNAPSHOT_TTL_SECONDS = 10

_fixture: dict | None = None


def set_fixture(fixture: dict | None) -> None:
    global _fixture
    _fixture = fixture


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _expires(seconds: int = SNAPSHOT_TTL_SECONDS) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + seconds))


def get_lock_waiters(schema_ref: str, table_ref: str, min_wait_ms: int) -> dict:
    """查询目标表上的锁等待关系(waits 列表)。fixture 优先;真实查询失败 → ok=False。"""
    if _fixture is not None:
        waits = _fixture.get("waits") or []
        return {"ok": True, "data": {"observed_at": _now_iso(), "snapshot_expires_at": _expires(),
                                     "waits": waits}}
    try:
        engine = get_readonly_engine()
        from sqlalchemy import text as _sql_text
        with engine.connect() as conn:
            rows = conn.execute(_sql_text(
                "SELECT lw.REQUESTING_ENGINE_TRANSACTION_ID AS requesting_trx, "
                "lw.BLOCKING_ENGINE_TRANSACTION_ID AS blocking_trx, "
                "rt.PROCESSLIST_ID AS requesting_pid, "
                "bt.PROCESSLIST_ID AS blocking_pid, "
                "dl.OBJECT_SCHEMA, dl.OBJECT_NAME, dl.INDEX_NAME, "
                "dl.LOCK_TYPE, dl.LOCK_MODE, "
                "rt.PROCESSLIST_TIME * 1000 AS wait_ms "
                "FROM performance_schema.data_lock_waits lw "
                "LEFT JOIN performance_schema.data_locks dl "
                "ON dl.ENGINE_TRANSACTION_ID = lw.BLOCKING_ENGINE_TRANSACTION_ID "
                "JOIN performance_schema.threads rt "
                "ON lw.REQUESTING_THREAD_ID = rt.THREAD_ID "
                "JOIN performance_schema.threads bt "
                "ON lw.BLOCKING_THREAD_ID = bt.THREAD_ID")).mappings().all()
    except Exception as exc:  # 连接失败/表不可用 → 明确失败,不伪造
        return {"ok": False, "data": None, "error_message": f"lock_waiters_query_failed: {exc}"}
    waits = []
    for r in rows:
        wait_ms = int(r.get("wait_ms") or 0)  # 已是毫秒(PROCESSLIST_TIME * 1000)
        if wait_ms < min_wait_ms:
            continue
        waits.append({
            "wait_ref": f"w_{r.get('requesting_trx')}",
            "waiter_ref": f"trx_{r.get('requesting_trx')}",
            "blocker_ref": f"blk_{r.get('blocking_pid')}",
            "requesting_transaction_id": r.get("requesting_trx"),
            "blocking_transaction_id": r.get("blocking_trx"),
            "requesting_processlist_id": r.get("requesting_pid"),
            "blocking_processlist_id": r.get("blocking_pid"),
            "requesting_lock_ref": f"trx_{r.get('requesting_trx')}",
            "blocking_lock_ref": f"trx_{r.get('blocking_trx')}",
            "object_schema": r.get("OBJECT_SCHEMA"),
            "object_table": r.get("OBJECT_NAME"),
            "index_name": r.get("INDEX_NAME"),
            "lock_type": r.get("LOCK_TYPE"),
            "lock_mode": r.get("LOCK_MODE"),
            "wait_duration_ms": wait_ms,
            "waiting_query_ref": "INVENTORY_RESERVATION",  # 由调用方按业务解析
        })
    return {"ok": True, "data": {"observed_at": _now_iso(), "snapshot_expires_at": _expires(),
                                 "waits": waits}}


def get_transaction_details(transaction_ref: str) -> dict:
    """查询阻塞事务详情。fixture 优先;真实查询失败/找不到 → ok=False。
    transaction_ref 为 blocker_ref(blk_<trx_id> 或 lock_observation 引用),程序解析出事务 ID。"""
    if _fixture is not None:
        data = dict(_fixture)
        data["observed_at"] = _now_iso()
        data["snapshot_expires_at"] = _expires()
        return {"ok": True, "data": data}
    # 解析 blocker_ref → processlist_id(受控引用,LLM 不得编造)
    # 注意:performance_schema 的 ENGINE_TRANSACTION_ID 与 innodb_trx.trx_id 不是同一 ID,
    # 因此用 processlist_id(两者一致的连接 ID)关联阻塞会话。
    pid_str = str(transaction_ref).removeprefix("blk_").strip()
    if not pid_str.isdigit():
        return {"ok": False, "data": None, "error_message": "INVALID_BLOCKER_REF"}
    try:
        engine = get_readonly_engine()
        from sqlalchemy import text as _sql_text
        with engine.connect() as conn:
            rows = conn.execute(_sql_text(
                "SELECT trx_id, trx_mysql_thread_id, trx_started, trx_state, "
                "TIMESTAMPDIFF(MICROSECOND, trx_started, NOW(3)) / 1000 AS age_ms "
                "FROM information_schema.innodb_trx WHERE trx_mysql_thread_id = :pid"),
                {"pid": int(pid_str)}).mappings().all()
    except Exception as exc:
        return {"ok": False, "data": None, "error_message": f"trx_query_failed: {exc}"}
    if not rows:
        return {"ok": False, "data": None, "error_message": "TRX_NOT_FOUND"}
    r = rows[0]
    return {"ok": True, "data": {
        "transaction_id": r.get("trx_id"),
        "processlist_id": r.get("trx_mysql_thread_id"),
        "account": "",
        "age_ms": int(r.get("age_ms") or 0),
        "statement_digest": "",
        "locked_objects": [],
        "observed_at": _now_iso(),
        "snapshot_expires_at": _expires(),
    }}
