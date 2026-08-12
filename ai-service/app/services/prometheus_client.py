"""PrometheusMetricsClient:只执行固定 PromQL 模板,不接收 LLM 生成的查询文本。"""
import time
import uuid

import httpx

from app.config import settings
from app.services import promql_templates

ERROR_METRICS_BACKEND_UNAVAILABLE = "METRICS_BACKEND_UNAVAILABLE"
ERROR_METRICS_NOT_FOUND = "METRICS_NOT_FOUND"
ERROR_METRICS_STALE = "METRICS_STALE"
ERROR_METRICS_RESULT_INVALID = "METRICS_RESULT_INVALID"


class PrometheusMetricsClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or settings.prometheus_url

    def query(self, query_template_id: str, labels: dict,
              window_seconds: int) -> list[dict]:
        tpl = promql_templates.TEMPLATES.get(query_template_id)
        if tpl is None:
            raise ValueError(ERROR_METRICS_RESULT_INVALID)
        expr = tpl["expr"] % labels
        try:
            with httpx.Client(base_url=self.base_url, timeout=10.0) as client:
                resp = client.post("/api/v1/query",
                                   data={"query": expr, "time": str(int(time.time()))})
                resp.raise_for_status()
                body = resp.json()
        except httpx.HTTPError as e:
            raise ValueError(ERROR_METRICS_BACKEND_UNAVAILABLE) from e
        if body.get("status") != "success":
            raise ValueError(ERROR_METRICS_BACKEND_UNAVAILABLE)
        result = body.get("data", {}).get("result", [])
        if not result:
            raise ValueError(ERROR_METRICS_NOT_FOUND)
        return result

    def _latest_sample_time(self, result: list[dict]) -> float:
        ts = 0.0
        for r in result:
            val = r.get("value") or []
            if val:
                ts = max(ts, float(val[0]))
        return ts

    def get_service_metrics(self, service_ref: str,
                            window_start: str, window_end: str) -> dict:
        obs_id = uuid.uuid4().hex[:12]
        evaluated_at = int(time.time())
        window = f"{int(settings.metrics_max_age_seconds * 2)}s"
        labels = {"service": service_ref, "uri": ".+", "method": "",
                  "status": "", "window": window}
        p95_rows = self.query("HTTP_SERVER_P95_V1", labels, 300)
        qps_rows = self.query("HTTP_SERVER_QPS_V1", labels, 300)
        err_rows = self.query("HTTP_SERVER_ERROR_RATE_V1", labels, 300)
        latest = self._latest_sample_time(p95_rows)
        if evaluated_at - latest > settings.metrics_max_age_seconds:
            raise ValueError(ERROR_METRICS_STALE)
        try:
            p95 = float(p95_rows[0].get("value", [0, 0])[1]) * 1000.0
            qps = float(qps_rows[0].get("value", [0, 0])[1])
            err = float(err_rows[0].get("value", [0, 0])[1])
        except (IndexError, TypeError, ValueError) as e:
            raise ValueError(ERROR_METRICS_RESULT_INVALID) from e
        return {
            "sourceBackend": "prometheus",
            "observationQueryId": obs_id,
            "queryTemplateId": "HTTP_SERVER_P95_V1",
            "windowStart": window_start,
            "windowEnd": window_end,
            "evaluatedAt": evaluated_at,
            "latestSampleAt": int(latest),
            "p95Ms": p95,
            "qps": qps,
            "errorRate": err,
        }
