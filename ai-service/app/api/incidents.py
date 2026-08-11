from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import (Approval, FixDefinition, FixExecution, FixProposal,
                           Postmortem, RecoveryCheck)
from app.repositories import evidence_repo, hypothesis_repo, incident_repo
from app.repositories.tool_repo import list_tool_calls
from app.services.health_baseline_service import capture_health_baseline

router = APIRouter(prefix="/api/incidents")


class IncidentIn(BaseModel):
    title: str
    description: str | None = None
    severity: str = "medium"
    service_ref: str
    observed_at: str | None = None


@router.post("", status_code=201)
def create_incident(payload: IncidentIn):
    inc = incident_repo.create_incident(
        payload.title, payload.description, payload.severity, payload.service_ref)
    # 健康指标基线:Incident 创建时从 Java 采集(失败为 None 不影响创建)
    health = capture_health_baseline(payload.service_ref)
    incident_repo.save_health_baseline(inc.id, health)
    return {"id": inc.id, "status": inc.status, "title": inc.title,
            "service_ref": inc.service_ref}


@router.get("")
def list_incidents():
    incidents = incident_repo.list_incidents()
    return [{"id": i.id, "title": i.title, "status": i.status,
             "severity": i.severity, "created_at": str(i.created_at)} for i in incidents]


@router.get("/{incident_id}")
def get_incident(incident_id: int):
    inc = incident_repo.get_incident(incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    hypotheses = hypothesis_repo.list_by_incident(incident_id)
    evidence = evidence_repo.list_by_incident(incident_id)
    with Session(get_control_engine()) as session:
        approvals = list(session.scalars(select(Approval).filter(
            Approval.incident_id == incident_id).order_by(Approval.id.desc())).all())
        proposals = list(session.scalars(select(FixProposal).filter(
            FixProposal.incident_id == incident_id).order_by(FixProposal.id.desc())).all())
        fix_execs = list(session.scalars(select(FixExecution).filter(
            FixExecution.incident_id == incident_id).order_by(FixExecution.id.desc())).all())
        checks = list(session.scalars(select(RecoveryCheck).filter(
            RecoveryCheck.incident_id == incident_id).order_by(RecoveryCheck.id.desc())).all())
        definitions = list(session.scalars(select(FixDefinition)).all())
        pms = list(session.scalars(select(Postmortem).filter(
            Postmortem.incident_id == incident_id).order_by(Postmortem.id.desc())).all())
    defn_by_id = {d.id: d for d in definitions}
    prop = proposals[0] if proposals else None
    tool_calls = list_tool_calls(incident_id)
    return {
        "id": inc.id, "title": inc.title, "status": inc.status,
        "severity": inc.severity, "service_ref": inc.service_ref,
        "created_at": str(inc.created_at),
        "finished_at": str(inc.finished_at) if inc.finished_at else None,
        "degraded": bool(inc.degraded),
        "degradation_reasons": (inc.degradation_reasons.split(",")
                                if inc.degradation_reasons else []),
        "termination_reason": inc.termination_reason,
        "hypotheses": [{"id": h.id, "description": h.description, "status": h.status}
                       for h in hypotheses],
        "evidence": [{"id": e.id, "source": e.source,
                      "key": (e.content or {}).get("key"),
                      "passed": (e.content or {}).get("passed"),
                      "content": (e.content or {}).get("data")} for e in evidence],
        "approvals": [{"id": a.id, "fix_proposal_id": a.fix_proposal_id,
                       "status": a.status, "approver": a.approver,
                       "comment": a.comment,
                       "expires_at": str(a.expires_at) if a.expires_at else None}
                      for a in approvals],
        "fix_proposal": ({
            "id": prop.id,
            "action_type": (defn_by_id.get(prop.fix_definition_id).action_name
                            if defn_by_id.get(prop.fix_definition_id) else None),
            "risk_level": prop.risk_level,
            "parameters_hash": prop.parameters_hash,
            "status": prop.status,
            "blocking_relation_hash": prop.blocking_relation_hash,
        } if prop else None),
        "fix_execution": ({
            "id": fix_execs[0].id, "fix_proposal_id": fix_execs[0].fix_proposal_id,
            "status": fix_execs[0].status,
        } if fix_execs else None),
        "recovery": ({
            "id": checks[0].id, "status": checks[0].status,
            "index_present": checks[0].index_present,
            "query_plan_uses_target_index": checks[0].query_plan_uses_target_index,
            "estimated_rows_after": checks[0].estimated_rows_after,
        } if checks else None),
        "report": (pms[0].content or {}) if pms else None,
        "tool_calls": [{"tool_name": tc.tool_name, "transport": tc.transport,
                        "agent_run_id": tc.agent_run_id,
                        "status": tc.status} for tc in (tool_calls or [])],
    }
