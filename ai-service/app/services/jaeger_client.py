"""JaegerTraceClient:search_traces / get_trace_by_id;固定搜索边界,服务/操作白名单由调用方保证。"""
from app.config import settings

ERROR_TRACE_BACKEND_UNAVAILABLE = "TRACE_BACKEND_UNAVAILABLE"
ERROR_TRACE_NOT_FOUND = "TRACE_NOT_FOUND"
ERROR_TRACE_RESULT_INVALID = "TRACE_RESULT_INVALID"


class JaegerTraceClient:
    def __init__(self, endpoint: str | None = None):
        self.endpoint = endpoint or settings.jaeger_query_endpoint

    def search_traces(self, service_ref: str, operation_ref: str,
                      start_time: str, end_time: str, strategy: str) -> list[dict]:
        request = {
            "service": service_ref,
            "operation": operation_ref,
            "start": start_time,
            "end": end_time,
            "limit": settings.max_trace_candidates,
            "strategy": strategy,
        }
        resp = _query_grpc(self.endpoint, request)
        traces = resp.get("traces") or []
        traces.sort(key=lambda t: _total_duration(t), reverse=(strategy == "SLOWEST"))
        return traces[:settings.max_trace_candidates]

    def get_trace_by_id(self, trace_id: str) -> dict:
        resp = _query_grpc(self.endpoint, {"trace_id": trace_id})
        trace = resp.get("trace")
        if not trace:
            raise ValueError(ERROR_TRACE_NOT_FOUND)
        return trace


def _query_grpc(endpoint: str, request: dict) -> dict:
    """Task 15 接入真实 Jaeger gRPC QueryService;此处为接口占位(测试 monkeypatch)。"""
    raise NotImplementedError("gRPC QueryService 实现在 Task 15")


def _total_duration(trace: dict) -> int:
    spans = trace.get("spans") or []
    return max((s.get("duration") or 0) for s in spans) if spans else 0
