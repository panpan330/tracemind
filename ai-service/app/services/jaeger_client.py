"""JaegerTraceClient:通过 Jaeger HTTP JSON API(固定版本 jaegertracing/jaeger:2.6.0)
查询 trace;search 边界固定,不接收 LLM 生成的查询条件。"""
import time
import uuid

import httpx

from app.config import settings

ERROR_TRACE_BACKEND_UNAVAILABLE = "TRACE_BACKEND_UNAVAILABLE"
ERROR_TRACE_NOT_FOUND = "TRACE_NOT_FOUND"
ERROR_TRACE_RESULT_INVALID = "TRACE_RESULT_INVALID"


class JaegerTraceClient:
    def __init__(self, endpoint: str | None = None, http_base: str | None = None):
        self.endpoint = endpoint or settings.jaeger_query_endpoint  # host:16685(gRPC 端口)
        host = self.endpoint.split(":")[0] if ":" in self.endpoint else self.endpoint
        self.http_base = http_base or f"http://{host}:16686"       # UI HTTP 端口

    def get_trace_by_id(self, trace_id: str) -> dict:
        try:
            with httpx.Client(base_url=self.http_base, timeout=10.0) as client:
                resp = client.get(f"/api/traces/{trace_id}")
                resp.raise_for_status()
                body = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ValueError(ERROR_TRACE_BACKEND_UNAVAILABLE) from e
        data = body.get("data") or []
        if not data:
            raise ValueError(ERROR_TRACE_NOT_FOUND)
        return data[0]

    def search_traces(self, service_ref: str, operation_ref: str,
                      start_time: str, end_time: str, strategy: str) -> list[dict]:
        """固定边界:窗口/候选数由调用方保证;按耗时排序取 top N。"""
        try:
            params = {
                "service": service_ref,
                "start": _epoch_us(start_time),
                "end": _epoch_us(end_time),
                "limit": settings.max_trace_candidates,
            }
            with httpx.Client(base_url=self.http_base, timeout=10.0) as client:
                resp = client.get("/api/traces", params=params)
                resp.raise_for_status()
                body = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ValueError(ERROR_TRACE_BACKEND_UNAVAILABLE) from e
        traces = body.get("data") or []
        traces.sort(key=lambda t: _total_duration(t), reverse=(strategy == "SLOWEST"))
        return traces[:settings.max_trace_candidates]

    def canary(self) -> str:
        """返回一个用于冒烟查询的 trace id(不存在时为 TRACE_NOT_FOUND,用于探活)。"""
        return f"canary-{uuid.uuid4().hex[:8]}"


def _total_duration(trace: dict) -> int:
    spans = trace.get("spans") or []
    return max((s.get("duration") or 0) for s in spans) if spans else 0


def _epoch_us(iso: str) -> int:
    try:
        import datetime
        dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1_000_000)
    except (ValueError, TypeError):
        return int(time.time() * 1_000_000)
