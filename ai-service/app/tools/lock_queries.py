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
        with engine.connect() as conn:
            rows = conn.execute(
                "SELECT REQUESTING_THREAD_ID, BLOCKING_THREAD_ID, "
                "REQUESTING_ENGINE_TRANSACTION_ID, BLOCKING_ENGINE_TRANSACTION_ID, "
                "REQUESTING_PROCESS_ID, BLOCKING_PROCESS_ID, "
                "OBJECT_SCHEMA, OBJECT_NAME, OBJECT_INDEX_NAME, "
                "LOCK_TYPE, LOCK_MODE, WAIT_TIME_MS "
                "FROM performance_schema.data_lock_waits"
            ).mappings().all()
    except Exception as exc:  # 连接失败/表不存在 → 明确失败,不伪造
        return {"ok": False, "data": None, "error_message": f"lock_waiters_query_failed: {exc}"}
    waits = []
    for r in rows:
        wait_ms = int(r.get("WAIT_TIME_MS") or 0)
        if wait_ms < min_wait_ms:
            continue
        if r.get("OBJECT_SCHEMA") != schema_ref or r.get("OBJECT_NAME") != table_ref:
            continue
        waits.append({
            "wait_ref": f"w_{r.get('REQUESTING_THREAD_ID')}",
            "waiter_ref": f"thr_{r.get('REQUESTING_THREAD_ID')}",
            "blocker_ref": f"blk_{uuid.uuid4().hex[:12]}",
            "requesting_transaction_id": r.get("REQUESTING_ENGINE_TRANSACTION_ID"),
            "blocking_transaction_id": r.get("BLOCKING_ENGINE_TRANSACTION_ID"),
            "requesting_processlist_id": r.get("REQUESTING_PROCESS_ID"),
            "blocking_processlist_id": r.get("BLOCKING_PROCESS_ID"),
            "requesting_lock_ref": str(r.get("REQUESTING_THREAD_ID")),
            "blocking_lock_ref": str(r.get("BLOCKING_THREAD_ID")),
            "object_schema": r.get("OBJECT_SCHEMA"),
            "object_table": r.get("OBJECT_NAME"),
            "index_name": r.get("OBJECT_INDEX_NAME"),
            "lock_type": r.get("LOCK_TYPE"),
            "lock_mode": r.get("LOCK_MODE"),
            "wait_duration_ms": wait_ms,
            "waiting_query_ref": "INVENTORY_RESERVATION",  # 由调用方按业务解析
        })
    return {"ok": True, "data": {"observed_at": _now_iso(), "snapshot_expires_at": _expires(),
                                 "waits": waits}}


def get_transaction_details(transaction_ref: str) -> dict:
    """查询阻塞事务详情。fixture 优先;真实查询失败/找不到 → ok=False。"""
    if _fixture is not None:
        data = dict(_fixture)
        data["observed_at"] = _now_iso()
        data["snapshot_expires_at"] = _expires()
        return {"ok": True, "data": data}
    try:
        engine = get_readonly_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                "SELECT trx_id, trx_mysql_thread_id, trx_started, trx_state, "
                "TIMESTAMPDIFF(MILLISECOND, trx_started, NOW(3)) AS age_ms "
                "FROM information_schema.innodb_trx WHERE trx_id = %s",
                (transaction_ref,),
            ).mappings().all()
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
