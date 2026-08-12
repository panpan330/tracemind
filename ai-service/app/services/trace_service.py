"""get_trace 后端门面:jaeger | fixture;trace_ref 由程序解析为搜索参数;V1.4 观测审计。"""
import hashlib
import json
import time

from app.config import settings
from app.repositories import observation_repo
from app.services.jaeger_client import JaegerTraceClient
from app.services.trace_normalizer import TraceNormalizer


def _resolve_incident_window(incident: dict) -> tuple[str, str]:
    """按 Incident 观测窗口搜索:observed_at 起至当前时间(最多 max_trace_search_window_seconds)。"""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    end = now.isoformat()
    start_iso = incident.get("observed_at") or end
    try:
        start_dt = datetime.datetime.fromisoformat(str(start_iso).replace("Z", "+00:00"))
    except ValueError:
        start_dt = now - datetime.timedelta(seconds=settings.max_trace_search_window_seconds)
    if (now - start_dt).total_seconds() > settings.max_trace_search_window_seconds:
        start_dt = now - datetime.timedelta(seconds=settings.max_trace_search_window_seconds)
    return start_dt.isoformat(), end


def get_trace(trace_ref: str | None, trace_id: str | None, incident: dict,
              incident_id: int = 0, agent_run_id: int = 0) -> dict:
    start = time.monotonic()
    service_ref = incident.get("affected_service_ref") or "inventory-service"
    operation_ref = incident.get("affected_operation_ref") or "INVENTORY_LOOKUP"
    error_code = None
    try:
        if settings.trace_backend == "jaeger":
            client = JaegerTraceClient()
            if trace_id:
                raw = client.get_trace_by_id(trace_id)
            else:
                start_w, end_w = _resolve_incident_window(incident)
                candidates = client.search_traces(service_ref, operation_ref,
                                                  start_w, end_w, "SLOWEST")
                if not candidates:
                    raise ValueError("TRACE_NOT_FOUND")
                # 候选逐个归一化:取首个结构完整的 trace(部分导出的锁超时 trace 可能不完整)
                raw, normalized = None, None
                for cand in candidates:
                    n = TraceNormalizer().normalize(cand, operation_ref)
                    if n.get("status") == "ok":
                        raw, normalized = cand, n
                        break
                if raw is None:
                    raise ValueError("TRACE_INCOMPLETE")
            out = {"sourceBackend": "jaeger", "traceId": raw.get("traceID"),
                   "observationQueryId": "obs-" + str(raw.get("traceID", ""))[:8],
                   **normalized}
        else:  # fixture
            out = {"sourceBackend": "fixture", "traceId": "fixture-trace-1",
                   "inventoryServiceDurationMs": 900, "targetDbDurationMs": 820,
                   "dbDominanceRatio": 0.91, "targetDbSpanId": "s3",
                   "normalizationRuleVersion": "TRACE_NORMALIZER_V1"}
        _audit(out, incident_id, agent_run_id, service_ref, operation_ref,
               trace_id, "ok", None, start)
        return out
    except ValueError as e:
        error_code = str(e)
        _audit({}, incident_id, agent_run_id, service_ref, operation_ref,
               trace_id, "error", error_code, start)
        raise


def _audit(out: dict, incident_id: int, agent_run_id: int,
           service_ref: str, operation_ref: str, trace_id: str | None,
           status: str, error_code: str | None, start: float) -> None:
    try:
        observation_repo.record_query(
            incident_id=incident_id, agent_run_id=agent_run_id,
            observation_query_id=out.get("observationQueryId", ""),
            backend=out.get("sourceBackend", "fixture"),
            query_template_id=None,
            normalized_params={"service_ref": service_ref, "operation_ref": operation_ref},
            window_start=None, window_end=None,
            status=status, error_code=error_code,
            duration_ms=int((time.monotonic() - start) * 1000),
            result_hash=(hashlib.sha256(json.dumps(out, ensure_ascii=False,
                                                   sort_keys=True).encode()).hexdigest()[:16]
                         if out else None),
            trace_id=trace_id or out.get("traceId"),
            normalized_result=out or None)
    except Exception:  # noqa: BLE001 审计失败不阻塞业务
        pass
