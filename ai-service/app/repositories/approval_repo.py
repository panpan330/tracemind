from datetime import timedelta

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import Approval, utcnow

APPROVAL_TTL_MINUTES = 10


def create_approval(incident_id: int, fix_proposal_id: int,
                    action_type: str, parameters_hash: str,
                    agent_run_id: int | None = None) -> Approval:
    with Session(get_control_engine()) as session:
        approval = Approval(
            incident_id=incident_id,
            # V2.0-A:审批绑定创建它的 Run(恢复/过期只处理该 Run)
            agent_run_id=agent_run_id,
            fix_proposal_id=fix_proposal_id,
            action_type=action_type,
            parameters_hash=parameters_hash,
            status="pending",
            expires_at=utcnow() + timedelta(minutes=APPROVAL_TTL_MINUTES),
        )
        session.add(approval)
        session.commit()
        session.refresh(approval)
        return approval


def get_approval(approval_id: int) -> Approval | None:
    with Session(get_control_engine()) as session:
        return session.get(Approval, approval_id)


def decide_approval_cas(approval_id: int, *, decision: str, approver: str,
                        comment: str | None, now_utc) -> Approval | None:
    """V2.0-A 原子 CAS 决定:仅 pending 且未过期可决定,时间用应用生成的 now_utc
    (不依赖 DB NOW(),规避服务器时区漂移)。返回 None = 已决定/过期/不存在。"""
    with Session(get_control_engine()) as session:
        res = session.execute(
            update(Approval)
            .where(
                Approval.id == approval_id,
                Approval.status == "pending",
                or_(Approval.expires_at.is_(None), Approval.expires_at > now_utc),
            )
            .values(status=decision, approver=approver, comment=comment))
        session.commit()
        if res.rowcount != 1:
            return None
        return session.get(Approval, approval_id)


def update_approval(approval_id: int, *, status: str, approver: str | None = None,
                    comment: str | None = None, consumed_at=None) -> Approval | None:
    """非 CAS 全量更新(保留给 scanner 标记 expired 等内部路径)。"""
    with Session(get_control_engine()) as session:
        approval = session.get(Approval, approval_id)
        if approval is None:
            return None
        approval.status = status
        if approver is not None:
            approval.approver = approver
        if comment is not None:
            approval.comment = comment
        if consumed_at is not None:
            approval.consumed_at = consumed_at
        session.commit()
        session.refresh(approval)
        return approval
