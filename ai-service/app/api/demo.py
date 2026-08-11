import httpx
from fastapi import APIRouter, HTTPException

from app.config import settings

# 通用场景路由:场景选择仅用于演示控制(inject/reset/status),不进入 Incident
router = APIRouter(prefix="/api/demo/scenarios")


def _proxy(scenario: str, action: str, method: str) -> dict:
    if not settings.demo_mode:
        raise HTTPException(403, "DEMO_MODE disabled")
    resp = httpx.request(method,
                         f"{settings.inventory_service_url}/internal/scenarios/{scenario}/{action}",
                         headers={"x-demo-key": settings.demo_key}, timeout=10)
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text)
    return resp.json()


@router.post("/{scenario}/inject")
def inject(scenario: str):
    return _proxy(scenario, "inject", "POST")


@router.post("/{scenario}/reset")
def reset(scenario: str):
    return _proxy(scenario, "reset", "POST")


@router.get("/{scenario}/status")
def status(scenario: str):
    """场景状态:代理到 Java 全局 status(返回 indexPresent/lockHeld/activeScenario)。"""
    if not settings.demo_mode:
        raise HTTPException(403, "DEMO_MODE disabled")
    resp = httpx.request("GET",
                         f"{settings.inventory_service_url}/internal/scenarios/status",
                         headers={"x-demo-key": settings.demo_key}, timeout=10)
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text)
    return resp.json()
