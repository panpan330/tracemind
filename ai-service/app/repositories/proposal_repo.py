from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import FixDefinition, FixProposal


def get_fix_definition_id(action_name: str) -> int:
    """按动作名查 fix_definition;找不到时回退到任意一条记录,再回退 1(V1.0 单场景)。"""
    with Session(get_control_engine()) as session:
        row = session.scalars(
            select(FixDefinition).filter(FixDefinition.action_name == action_name).limit(1)
        ).first()
        if row is not None:
            return row.id
        any_row = session.scalars(select(FixDefinition).limit(1)).first()
        return any_row.id if any_row is not None else 1


def create_proposal(incident_id: int, action_type: str, risk_level: str,
                    parameters: dict, parameters_hash: str, reason: str | None = None,
                    blocking_relation_hash: str | None = None) -> FixProposal:
    with Session(get_control_engine()) as session:
        fix_definition_id = get_fix_definition_id(action_type)
        proposal = FixProposal(
            incident_id=incident_id,
            fix_definition_id=fix_definition_id,
            parameters_json=parameters,
            parameters_hash=parameters_hash,
            blocking_relation_hash=blocking_relation_hash,
            risk_level=risk_level,
            reason=reason,
            status="proposed",
        )
        session.add(proposal)
        session.commit()
        session.refresh(proposal)
        return proposal


def get_proposal(proposal_id: int) -> FixProposal | None:
    with Session(get_control_engine()) as session:
        return session.get(FixProposal, proposal_id)
