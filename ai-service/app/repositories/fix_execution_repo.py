"""fix_execution 审计写入(control 库)。"""
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.engine import get_control_engine

# 惰性获取:函数内调用 get_control_engine()(offline_eval 下模块导入不触 DB)


def build_idempotency_key(*, incident_id: int, fix_proposal_id: int | None,
                          approval_id: int | None,
                          parameters_hash: str | None) -> str:
    """幂等键必须绑定审批/Incident 维度:裸 parameters_hash 在不同 Incident
    重复同一动作(如两次建同一索引)时撞 uq_fix_idem,导致第二次审计被吞。
    - approval_id 非空:appr:{approval_id}(一次审批恰一次执行审计);
    - 回退(无审批,如历史手动路径):inc:{incident}:prop:{proposal}:{hash}。
    前缀与历史裸键天然不冲突,无需数据迁移。"""
    if approval_id:
        return f"appr:{int(approval_id)}"
    return (f"inc:{int(incident_id)}:prop:{fix_proposal_id or 'x'}:"
            f"{parameters_hash or 'none'}")


def create_execution(*, incident_id: int, fix_proposal_id: int | None,
                     approval_id: int | None, idempotency_key: str,
                     blocking_relation_hash: str, status: str,
                     execution_result: str | None, kill_attempted: bool,
                     actual_processlist_id: int | None) -> dict:
    control_engine = get_control_engine()
    try:
        with control_engine.begin() as conn:
            # V2.1-D:text() 只支持命名参数;旧 SQL 用 "?" 占位 + 元组传参,
            # 参数从未正确绑定(此前被列缺失错误遮蔽)。
            conn.execute(text(
                "INSERT INTO fix_execution (incident_id, fix_proposal_id, approval_id, "
                "idempotency_key, blocking_relation_hash, status, execution_result, "
                "kill_attempted, actual_processlist_id, finished_at) "
                "VALUES (:incident_id, :fix_proposal_id, :approval_id, "
                ":idempotency_key, :blocking_relation_hash, :status, "
                ":execution_result, :kill_attempted, :actual_processlist_id, NOW(3))"),
                {"incident_id": incident_id, "fix_proposal_id": fix_proposal_id,
                 "approval_id": approval_id, "idempotency_key": idempotency_key,
                 "blocking_relation_hash": blocking_relation_hash,
                 "status": status, "execution_result": execution_result,
                 "kill_attempted": int(kill_attempted),
                 "actual_processlist_id": actual_processlist_id})
    except IntegrityError as exc:
        # 同幂等键重复请求(重复审计):显式 duplicate 语义,不产生第二行,
        # 也不掩盖其他写失败(列缺失/连接断等非冲突错误原样抛出)。
        if "uq_fix_idem" not in str(exc):
            raise
        return {"idempotency_key": idempotency_key, "status": "duplicate"}
    return {"idempotency_key": idempotency_key, "status": status}
