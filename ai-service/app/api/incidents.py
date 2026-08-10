from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.engine import get_readonly_engine
from app.repositories import incident_repo
from app.services.baseline_service import capture_digest_baseline

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
    baseline = capture_digest_baseline(get_readonly_engine())
    incident_repo.save_incident_baseline(inc.id, baseline)
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
    return {"id": inc.id, "title": inc.title, "status": inc.status,
            "severity": inc.severity, "service_ref": inc.service_ref,
            "created_at": str(inc.created_at), "finished_at": str(inc.finished_at) if inc.finished_at else None}
