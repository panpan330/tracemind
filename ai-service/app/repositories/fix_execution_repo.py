"""fix_execution 审计写入(control 库)。"""
from sqlalchemy import text
from app.db.engine import get_control_engine

# 惰性获取:函数内调用 get_control_engine()(offline_eval 下模块导入不触 DB)


def create_execution(*, incident_id: int, fix_proposal_id: int | None,
                     approval_id: int | None, idempotency_key: str,
                     blocking_relation_hash: str, status: str,
                     execution_result: str | None, kill_attempted: bool,
                     actual_processlist_id: int | None) -> dict:
    control_engine = get_control_engine()
    with control_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO fix_execution (incident_id, fix_proposal_id, approval_id, "
            "idempotency_key, blocking_relation_hash, status, execution_result, "
            "kill_attempted, actual_processlist_id, finished_at) "
            "VALUES (?,?,?,?,?,?,?,?,?, NOW(3))"),
            (incident_id, fix_proposal_id, approval_id, idempotency_key,
             blocking_relation_hash, status, execution_result,
             int(kill_attempted), actual_processlist_id))
    return {"idempotency_key": idempotency_key, "status": status}
