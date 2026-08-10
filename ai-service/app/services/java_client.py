import httpx

from app.config import settings

_ORDER = settings.order_service_url
_INVENTORY = settings.inventory_service_url


def _base_url(service_ref: str) -> str:
    if service_ref == "order-service":
        return _ORDER
    if service_ref == "inventory-service":
        return _INVENTORY
    raise ValueError(f"UNKNOWN_SERVICE_REF: {service_ref}")


def get_metrics(service_ref: str, window_seconds: int) -> dict:
    resp = httpx.get(f"{_base_url(service_ref)}/internal/observations/metrics",
                     params={"window_seconds": window_seconds}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_trace_records(service_ref: str, trace_id: str) -> list[dict] | None:
    """返回该服务对 trace_id 的观测记录;无记录返回 None(TRACE_NOT_FOUND 语义)。"""
    resp = httpx.get(f"{_base_url(service_ref)}/internal/observations/traces/{trace_id}",
                     timeout=10)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()
