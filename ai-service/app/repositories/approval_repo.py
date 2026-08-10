from datetime import timedelta

from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import Approval, utcnow

APPROVAL_TTL_MINUTES = 10


def create_approval(incident_id: int, fix_proposal_id: int,
                    action_type: str, parameters_hash: str) -> Approval:
    with Session(get_control_engine()) as session:
        approval = Approval(
            incident_id=incident_id,
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


def update_approval(approval_id: int, *, status: str, approver: str | None = None,
                    comment: str | None = None, consumed_at=None) -> Approval | None:
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
