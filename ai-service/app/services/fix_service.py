from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine, get_executor_engine
from app.db.models import Approval, FixExecution, FixProposal, utcnow

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

        action = FIX_ACTIONS.get(proposal.action_type if hasattr(proposal, "action_type") else "CREATE_INVENTORY_INDEX")
        if action is None:
            raise ValueError("UNKNOWN_FIX_ACTION")

        idempotency_key = f"{incident_id}:{fix_proposal_id}:{proposal.parameters_hash}"
        existing = session.scalars(select(FixExecution).filter(
            FixExecution.idempotency_key == idempotency_key)).first()
        if existing is not None and existing.status == "succeeded":
            return {"status": "no_op", "detail": "already_executed",
                    "fix_execution_id": existing.id}

        # 索引已存在 → no_op,不重复创建
        with get_executor_engine().connect() as conn:
            present = conn.execute(text(action["check_sql"])).scalar_one()
            if present:
                execution = FixExecution(incident_id=incident_id, fix_proposal_id=fix_proposal_id,
                                         approval_id=approval_id, idempotency_key=idempotency_key,
                                         status="no_op", result={"detail": "index already present"})
                session.add(execution)
                session.commit()
                session.refresh(execution)
                return {"status": "no_op", "fix_execution_id": execution.id}

            conn.execute(text(action["ddl"]))

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
