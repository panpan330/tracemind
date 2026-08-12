"""get_service_metrics 后端门面:prometheus | fixture。"""
from app.config import settings
from app.services.prometheus_client import PrometheusMetricsClient


def get_metrics(service_ref: str, window_start: str, window_end: str) -> dict:
    if settings.metrics_backend == "prometheus":
        return PrometheusMetricsClient().get_service_metrics(service_ref, window_start, window_end)
    return {"sourceBackend": "fixture", "p95Ms": 2, "qps": 10.0, "errorRate": 0.0,
            "windowStart": window_start, "windowEnd": window_end,
            "observationQueryId": "fixture-0"}
