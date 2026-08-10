from app.services.java_client import get_trace_records


def get_trace(trace_id: str) -> dict:
    """E2:组合 order-service 与 inventory-service 的观测记录。"""
    order_records = get_trace_records("order-service", trace_id)
    inventory_records = get_trace_records("inventory-service", trace_id)
    if order_records is None and inventory_records is None:
        raise ValueError("TRACE_NOT_FOUND")
    return {
        "trace_id": trace_id,
        "order_service": order_records or [],
        "inventory_service": inventory_records or [],
    }
