from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine, get_executor_engine
from app.db.models import Approval, FixExecution, FixProposal, utcnow
from app.repositories import incident_repo
from app.services import preflight

# 预定义修复动作目录(fix_definition 元数据 + 代码内固化 DDL 模板)
FIX_ACTIONS = {
    "CREATE_INVENTORY_INDEX": {
        "risk_level": "medium",
        "ddl": "CREATE INDEX idx_sku_warehouse ON inventory (sku_id, warehouse_id)",
        "check_sql": ("SELECT COUNT(*) FROM information_schema.statistics "
                      "WHERE table_schema = DATABASE() AND table_name = 'inventory' "
                      "AND index_name = 'idx_sku_warehouse'"),
    },
}


def _index_present() -> bool:
    """目标索引是否已存在(幂等 no_op 判据)。"""
    with get_executor_engine().connect() as conn:
        return conn.execute(text(FIX_ACTIONS["CREATE_INVENTORY_INDEX"]["check_sql"])
                            ).scalar_one() > 0


def _index_plan_ok() -> bool:
    """前置条件复核:目标查询计划仍为全表扫描(索引缺失的实测证据)。"""
    try:
        with get_executor_engine().connect() as conn:
            row = conn.execute(text(
                "EXPLAIN FORMAT=JSON SELECT id FROM inventory "
                "WHERE sku_id = 42 AND warehouse_id = 7")).fetchone()
        import json
        plan = json.loads(row[0]) if row and isinstance(row[0], str) else None
        access_type = plan["query_block"]["table"].get("access_type") if plan else None
        return access_type == "ALL"
    except Exception:  # noqa: BLE001 复核失败不阻断执行(既有审批/幂等已保证安全)
        return True


def _execute_ddl(ddl: str) -> None:
    with get_executor_engine().connect() as conn:
        conn.execute(text(ddl))


def execute_fix(incident_id: int, fix_proposal_id: int, approval_id: int) -> dict:
    """执行预定义修复动作(唯一写路径)。

    M2 骨架:校验 Approval 已批准且未过期,幂等键去重,no_op 支持。
    M3 将接入 LangGraph 状态机(incident 处于 awaiting_approval 等完整校验)。
    """
    with Session(get_control_engine()) as session:
        approval = session.get(Approval, approval_id)
        proposal = session.get(FixProposal, fix_proposal_id)
        if approval is None or proposal is None:
            raise ValueError("APPROVAL_OR_PROPOSAL_NOT_FOUND")
        if approval.status != "approved":
            raise ValueError("APPROVAL_NOT_APPROVED")
        if approval.incident_id != incident_id or proposal.incident_id != incident_id:
            raise ValueError("APPROVAL_INCIDENT_MISMATCH")
        if approval.expires_at and approval.expires_at < utcnow():
            raise ValueError("APPROVAL_EXPIRED")

        # V2.0-A fail closed:未知/缺失 action 一律拒绝(不再默认 CREATE_INVENTORY_INDEX)
        action = FIX_ACTIONS.get(proposal.action_type)
        if action is None:
            raise ValueError("UNKNOWN_FIX_ACTION")

        idempotency_key = f"{incident_id}:{fix_proposal_id}:{proposal.parameters_hash}"
        existing = session.scalars(select(FixExecution).filter(
            FixExecution.idempotency_key == idempotency_key)).first()
        if existing is not None and existing.status == "succeeded":
            return {"status": "no_op", "detail": "already_executed",
                    "fix_execution_id": existing.id}

        # 索引已存在 → no_op,不重复创建(V2.1-C:Preflight 不介入 no_op 路径,
        # 保持既有安全幂等语义)
        if _index_present():
            execution = FixExecution(incident_id=incident_id, fix_proposal_id=fix_proposal_id,
                                     approval_id=approval_id, idempotency_key=idempotency_key,
                                     status="no_op", result={"detail": "index already present"})
            session.add(execution)
            session.commit()
            session.refresh(execution)
            return {"status": "no_op", "fix_execution_id": execution.id}

        # V2.1-C Preflight:写操作前用当前实测状态复核(已自愈 → 拒绝,零写操作)
        pf = preflight.preflight_for_index(incident_id, proposal.parameters_json
                                           if isinstance(proposal.parameters_json, dict)
                                           else None)
        if not pf.ok:
            incident_repo.update_status(incident_id, "needs_human",
                                        termination_reason=pf.reason[:64])
            raise ValueError(pf.reason)
        _execute_ddl(action["ddl"])

        execution = FixExecution(incident_id=incident_id, fix_proposal_id=fix_proposal_id,
                                 approval_id=approval_id, idempotency_key=idempotency_key,
                                 status="succeeded", result={"detail": "index created"})
        session.add(execution)
        session.commit()
        session.refresh(execution)
        # 标记审批已消费
        approval.status = "consumed"
        approval.consumed_at = utcnow()
        session.commit()
        return {"status": "succeeded", "fix_execution_id": execution.id}
