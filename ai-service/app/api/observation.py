from fastapi import APIRouter, HTTPException

from app.repositories import incident_repo
from app.services.observation_service import build_run_observation

router = APIRouter(prefix="/api/incidents")


@router.get("/{incident_id}/runs/{run_id}/observation")
def get_run_observation(incident_id: int, run_id: int) -> dict:
    if incident_repo.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return build_run_observation(incident_id, run_id)
