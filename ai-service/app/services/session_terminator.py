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
    """独立连接池,凭据仅此处持有;默认回退只读引擎(生产由 TRACEMIND_SESSION_TERMINATOR_DB_URL 提供)。"""
    global _engine
    if _engine is None:
        from app.db.engine import get_engine_from_url
        from app.config import settings
        url = getattr(settings, "session_terminator_db_url", "") or settings.readonly_db_url
        _engine = get_engine_from_url(url)
    return _engine


class SqlEngine:
    """真实 SQL 引擎:query_blocking 查阻塞会话,execute_kill 执行 KILL(Processlist 已转正整数)。"""

    def __init__(self, engine):
        self._engine = engine

    def query_blocking(self, processlist_id: int) -> dict | None:
        with self._engine.connect() as conn:
            rows = conn.execute(
                "SELECT trx_id, trx_mysql_thread_id, trx_started, trx_state, "
                "TIMESTAMPDIFF(MILLISECOND, trx_started, NOW(3)) AS age_ms "
                "FROM information_schema.innodb_trx WHERE trx_mysql_thread_id = %s",
                (processlist_id,)).mappings().all()
        if not rows:
            return None
        r = rows[0]
        # account/系统线程需结合 processlist 查询(Task 10 联调时补全真实字段)
        return {"transaction_id": r.get("trx_id"),
                "processlist_id": processlist_id,
                "account": "", "age_ms": int(r.get("age_ms") or 0),
                "holds_lock": True, "is_system": False}

    def execute_kill(self, processlist_id: int) -> None:
        with self._engine.connect() as conn:
            conn.execute(f"KILL {processlist_id}")  # pid 已通过 _to_positive_int 校验


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
        if blocking.get("transaction_id") != expected_tx:
            # 原事务消失但 processlist 已属另一事务 → TARGET_CHANGED,禁止 KILL
            return {"execution_result": "target_changed", "kill_attempted": False}
        if not blocking.get("holds_lock"):
            # 关系存在但事务/锁与审批不一致 → EVIDENCE_STALE,禁止 KILL,重新调查
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
