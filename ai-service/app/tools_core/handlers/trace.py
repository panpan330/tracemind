"""get_trace handler:经 TracePort 端口获取数据(不含业务实现)。"""
from app.tools_core.errors import ToolBusinessError


from app.tools_core.ports import TracePort

def build(ports: dict) -> dict:
    t = ports.get("trace")

    def get_trace(trace_ref: str | None = None, trace_id: str | None = None,
                  incident_id: int = 0) -> dict:
        # V2.0-A closure:incident_id 属可信上下文,由 ToolExecutionService 注入;
        # 端口适配器据此解析受控 service/operation(trace_service 缺失即 fail closed)
        if t is None:
            raise ToolBusinessError("PORT_UNAVAILABLE", "trace 端口未配置", retryable=False)
        try:
            return t.get_trace(trace_ref, trace_id, {}, incident_id=incident_id)
        except ToolBusinessError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ToolBusinessError("TRACE_QUERY_FAILED", str(e), retryable=True) from e

    return {"get_trace": get_trace}
