"""get_trace 后端门面:jaeger | fixture;trace_ref 由程序解析为搜索参数。"""
from app.config import settings
from app.services.jaeger_client import JaegerTraceClient
from app.services.trace_normalizer import TraceNormalizer


def _resolve_incident_window(incident: dict) -> tuple[str, str]:
    start = incident.get("observed_at") or "2026-08-12T00:00:00Z"
    return start, "2026-08-12T00:05:00Z"  # Task 15 按 Incident 真实窗口回填


def get_trace(trace_ref: str | None, trace_id: str | None, incident: dict) -> dict:
    service_ref = incident.get("affected_service_ref") or "inventory-service"
    operation_ref = incident.get("affected_operation_ref") or "INVENTORY_LOOKUP"
    if settings.trace_backend == "jaeger":
        client = JaegerTraceClient()
        if trace_id:
            raw = client.get_trace_by_id(trace_id)
        else:
            start, end = _resolve_incident_window(incident)
            candidates = client.search_traces(service_ref, operation_ref, start, end, "SLOWEST")
            if not candidates:
                raise ValueError("TRACE_NOT_FOUND")
            raw = client.get_trace_by_id(candidates[0]["traceID"])
        normalized = TraceNormalizer().normalize(raw, operation_ref)
        if normalized.get("status") == "TRACE_INCOMPLETE":
            raise ValueError("TRACE_INCOMPLETE")
        return {"sourceBackend": "jaeger", "traceId": raw.get("traceID"),
                "observationQueryId": "obs-" + str(raw.get("traceID", ""))[:8], **normalized}
    return {"sourceBackend": "fixture", "traceId": "fixture-trace-1",
            "inventoryServiceDurationMs": 900, "targetDbDurationMs": 820,
            "dbDominanceRatio": 0.91, "targetDbSpanId": "s3",
            "normalizationRuleVersion": "TRACE_NORMALIZER_V1"}
