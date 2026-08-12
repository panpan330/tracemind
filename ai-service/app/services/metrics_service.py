"""get_service_metrics 后端门面:prometheus | fixture;V1.4 观测审计(不存原始响应)。"""
import hashlib
import json
import time

from app.config import settings
from app.repositories import observation_repo
from app.services.prometheus_client import (PrometheusMetricsClient,
                                            ERROR_METRICS_BACKEND_UNAVAILABLE,
                                            ERROR_METRICS_NOT_FOUND,
                                            ERROR_METRICS_RESULT_INVALID,
                                            ERROR_METRICS_STALE)


def get_metrics(service_ref: str, window_start: str, window_end: str,
                incident_id: int = 0, agent_run_id: int = 0) -> dict:
    start = time.monotonic()
    try:
        if settings.metrics_backend == "prometheus":
            out = PrometheusMetricsClient().get_service_metrics(
                service_ref, window_start, window_end)
        else:  # fixture
            out = {"sourceBackend": "fixture", "p95Ms": 2, "qps": 10.0, "errorRate": 0.0,
                   "windowStart": window_start, "windowEnd": window_end,
                   "observationQueryId": f"fixture-{int(time.time())}"}
        observation_repo.record_query(
            incident_id=incident_id, agent_run_id=agent_run_id,
            observation_query_id=out.get("observationQueryId", ""),
            backend=out.get("sourceBackend", "fixture"),
            query_template_id=out.get("queryTemplateId"),
            normalized_params={"service_ref": service_ref},
            window_start=window_start, window_end=window_end,
            status="ok", error_code=None,
            duration_ms=int((time.monotonic() - start) * 1000),
            result_hash=hashlib.sha256(
                json.dumps(out, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16],
            trace_id=None,
            normalized_result=out)
        return out
    except ValueError as e:
        error_code = str(e)
        if error_code not in (ERROR_METRICS_STALE, ERROR_METRICS_NOT_FOUND,
                              ERROR_METRICS_BACKEND_UNAVAILABLE, ERROR_METRICS_RESULT_INVALID):
            error_code = ERROR_METRICS_BACKEND_UNAVAILABLE
        try:
            observation_repo.record_query(
                incident_id=incident_id, agent_run_id=agent_run_id,
                observation_query_id="", backend="prometheus", query_template_id=None,
                normalized_params={"service_ref": service_ref},
                window_start=window_start, window_end=window_end,
                status="error", error_code=error_code,
                duration_ms=int((time.monotonic() - start) * 1000),
                result_hash=None, trace_id=None, normalized_result=None)
        except Exception:  # noqa: BLE001 审计失败不阻塞业务
            pass
        raise
