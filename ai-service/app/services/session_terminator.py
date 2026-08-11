"""TERMINATE_BLOCKING_SESSION 执行器:防误杀(执行前重查/三结果/禁止账号)+ 原子幂等(进程内唯一执行键)。
session_terminator 账号凭据仅在本模块持有(环境变量 TRACEMIND_SESSION_TERMINATOR_DB_URL)。
设计 V1.3 §5.3~5.6。"""
import threading

ALLOWED_ACCOUNTS = frozenset({"app_business"})
FORBIDDEN_ACCOUNTS = frozenset({"tracemind_control_app", "ai_investigator",
                                "fix_executor", "session_terminator",
                                "root", "system user", "event_scheduler"})

_lock = threading.Lock()
_executed_keys: set[str] = set()   # 进程内幂等;DB 幂等见 fix_execution 表(uk_idempotency)

_engine = None


def get_terminator_engine():
    """独立连接池,凭据仅此处持有;默认回退只读引擎(生产由 TRACEMIND_SESSION_TERMINATOR_DB_URL 提供)。
    返回 SqlEngine 封装(提供 query_blocking/execute_kill)。"""
    global _engine
    if _engine is None:
        from app.db.engine import get_engine_from_url
        from app.config import settings
        url = getattr(settings, "session_terminator_db_url", "") or settings.readonly_db_url
        _engine = SqlEngine(get_engine_from_url(url))
    return _engine


class SqlEngine:
    """真实 SQL 引擎:query_blocking 查阻塞会话,execute_kill 执行 KILL(Processlist 已转正整数)。"""

    def __init__(self, engine):
        self._engine = engine

    def query_blocking(self, processlist_id: int) -> dict | None:
        from sqlalchemy import text
        with self._engine.connect() as conn:
            rows = conn.execute(
                text("SELECT trx.trx_id, trx.trx_mysql_thread_id, trx.trx_started, "
                     "TIMESTAMPDIFF(MICROSECOND, trx.trx_started, NOW(3)) / 1000 AS age_ms, "
                     "p.user AS account "
                     "FROM information_schema.innodb_trx trx "
                     "JOIN performance_schema.threads t "
                     "  ON t.PROCESSLIST_ID = trx.trx_mysql_thread_id "
                     "JOIN performance_schema.processlist p "
                     "  ON p.ID = trx.trx_mysql_thread_id "
                     "WHERE trx.trx_mysql_thread_id = :pid"),
                {"pid": processlist_id}).mappings().all()
            # holds_lock:该 processlist 是否仍作为阻塞者出现在锁等待中(实时关系重查)
            n = conn.execute(
                text("SELECT COUNT(*) FROM performance_schema.data_lock_waits lw "
                     "JOIN performance_schema.threads bt "
                     "  ON lw.BLOCKING_THREAD_ID = bt.THREAD_ID "
                     "WHERE bt.PROCESSLIST_ID = :pid"),
                {"pid": processlist_id}).scalar()
        if not rows:
            return None
        r = rows[0]
        account = r.get("account") or ""
        return {"transaction_id": r.get("trx_id"),
                "processlist_id": processlist_id,
                "account": account, "age_ms": int(r.get("age_ms") or 0),
                "holds_lock": bool(n),
                "is_system": account in ("system user", "event_scheduler")}

    def execute_kill(self, processlist_id: int) -> None:
        from sqlalchemy import text
        with self._engine.connect() as conn:
            conn.execute(text(f"KILL {processlist_id}"))  # pid 已通过 _to_positive_int 校验


def _to_positive_int(value) -> int | None:
    """Processlist 转正整数;不接受字符串 SQL 或任意连接标识符。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def execute(proposal: dict, approval: dict, engine=None) -> dict:
    """执行前重查(8 项语义)+ 三结果 + 幂等。返回执行结果,绝不抛异常(由调用方落库)。"""
    eng = engine if engine is not None else get_terminator_engine()
    if approval.get("status") != "approved":
        return {"execution_result": "rejected_not_approved", "kill_attempted": False}
    if approval.get("expires_at") and approval["expires_at"] < _now_iso():
        return {"execution_result": "rejected_expired", "kill_attempted": False}
    params = proposal.get("parameters") or {}
    pid = _to_positive_int(params.get("processlist_id"))
    if pid is None:
        return {"execution_result": "invalid_target", "kill_attempted": False}
    expected_tx = params.get("blocking_transaction_id")

    with _lock:
        idem_key = f"{proposal.get('parameters_hash')}:{pid}"
        if idem_key in _executed_keys:
            return {"execution_result": "already_executed", "kill_attempted": False}
        blocking = eng.query_blocking(pid)
        if blocking is None:
            # 原事务消失且原等待关系消失 → ALREADY_RESOLVED(安全无操作,防连接复用误杀)
            return {"execution_result": "already_resolved", "kill_attempted": False}
        # blocking_transaction_id 取 L2 证据的真实 innodb_trx.trx_id(与 query_blocking 同 ID 空间)
        if expected_tx is not None and str(blocking.get("transaction_id")) != str(expected_tx):
            # 原事务消失但 processlist 已属另一事务 → TARGET_CHANGED,禁止 KILL
            return {"execution_result": "target_changed", "kill_attempted": False}
        if not blocking.get("holds_lock"):
            # 该 processlist 已不再作为阻塞者持锁 → EVIDENCE_STALE,禁止 KILL,重新调查
            return {"execution_result": "evidence_stale", "kill_attempted": False}
        account = blocking.get("account") or ""
        if account in FORBIDDEN_ACCOUNTS or account not in ALLOWED_ACCOUNTS:
            return {"execution_result": "rejected_forbidden_account", "kill_attempted": False}
        if blocking.get("is_system"):
            return {"execution_result": "rejected_system_thread", "kill_attempted": False}
        eng.execute_kill(pid)
        _executed_keys.add(idem_key)
        return {"execution_result": "executed", "kill_attempted": True,
                "actual_processlist_id": pid}


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
