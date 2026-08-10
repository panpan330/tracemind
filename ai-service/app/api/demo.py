import httpx
from fastapi import APIRouter, HTTPException

from app.config import settings

router = APIRouter(prefix="/api/demo/scenarios/SCN-001")


def _proxy(action: str, method: str) -> dict:
    if not settings.demo_mode:
        raise HTTPException(403, "DEMO_MODE disabled")
    resp = httpx.request(method,
                         f"{settings.inventory_service_url}/internal/scenarios/SCN-001/{action}",
                         headers={"x-demo-key": settings.demo_key}, timeout=10)
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text)
    return resp.json()


@router.post("/inject")
def inject():
    return _proxy("inject", "POST")


@router.post("/reset")
def reset():
    return _proxy("reset", "POST")


@router.get("/status")
def status():
    return _proxy("status", "GET")
