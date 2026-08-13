"""get_service_metrics handler:经 MetricsPort 端口获取数据(不含业务实现)。"""
from app.tools_core.errors import ToolBusinessError


from app.tools_core.ports import MetricsPort

def build(ports: dict) -> dict:
    m = ports.get("metrics")

    def get_service_metrics(service_ref: str, window_seconds: int | None = None,
                            window_start: str | None = None,
                            window_end: str | None = None) -> dict:
        if m is None:
            raise ToolBusinessError("PORT_UNAVAILABLE", "metrics 端口未配置", retryable=False)
        import datetime
        end = window_end or datetime.datetime.now(datetime.timezone.utc).isoformat()
        start = window_start or (datetime.datetime.now(datetime.timezone.utc)
                                 - datetime.timedelta(seconds=window_seconds or 300)).isoformat()
        try:
            return m.get_metrics(service_ref, start, end, incident_id=0)
        except ToolBusinessError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ToolBusinessError("METRICS_QUERY_FAILED", str(e), retryable=True) from e

    return {"get_service_metrics": get_service_metrics}
