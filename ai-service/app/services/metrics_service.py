from app.services.java_client import get_metrics as _get_metrics


def get_metrics(service_ref: str, window_seconds: int) -> dict:
    """E1:目标服务 P95 相对健康基线异常(get_service_metrics)。"""
    return _get_metrics(service_ref, window_seconds)
