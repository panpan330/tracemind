"""get_trace handler:经 TracePort 端口获取数据(不含业务实现)。"""
from app.tools_core.errors import ToolBusinessError


from app.tools_core.ports import TracePort

def build(ports: dict) -> dict:
    t = ports.get("trace")

    def get_trace(trace_ref: str | None = None, trace_id: str | None = None) -> dict:
        if t is None:
            raise ToolBusinessError("PORT_UNAVAILABLE", "trace 端口未配置", retryable=False)
        try:
            return t.get_trace(trace_ref, trace_id, {}, incident_id=0)
        except ToolBusinessError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ToolBusinessError("TRACE_QUERY_FAILED", str(e), retryable=True) from e

    return {"get_trace": get_trace}
