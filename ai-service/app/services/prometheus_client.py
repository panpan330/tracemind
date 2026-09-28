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
        return self._instant(query_template_id, labels, window_seconds,
                             at_time=time.time())

    def query_at(self, query_template_id: str, labels: dict, window_seconds: int,
                 at_time: float) -> list[dict]:
        """V2.1-C:显式时间点的 instant 查询(历史窗口基线采集)。
        仍只执行固定模板;at_time 为 epoch 秒。"""
        return self._instant(query_template_id, labels, window_seconds, at_time=at_time)

    def sample_count(self, query_template_id: str, labels: dict,
                     start: float, end: float) -> int:
        """V2.1-C:窗口内请求样本数(count_over_time 固定模板,评估点=end)。
        用于最小样本数校验;start/end 为 epoch 秒。"""
        window = max(1, int(end - start))
        rows = self._instant("HTTP_SERVER_REQ_COUNT_V1", labels, window, at_time=end)
        try:
            return int(float(rows[0].get("value", [0, "0"])[1]))
        except (IndexError, TypeError, ValueError):
            return 0

    def _instant(self, query_template_id: str, labels: dict, window_seconds: int,
                 *, at_time: float) -> list[dict]:
        tpl = promql_templates.TEMPLATES.get(query_template_id)
        if tpl is None:
            raise ValueError(ERROR_METRICS_RESULT_INVALID)
        expr = tpl["expr"] % labels
        try:
            with httpx.Client(base_url=self.base_url, timeout=10.0) as client:
                resp = client.post("/api/v1/query",
                                   data={"query": expr, "time": str(int(at_time))})
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
        # V2.0-A:uri 用 operation registry 的真实模板正则(原 uri=~".+" 通配已删除,
        # 禁止通配掩盖映射错误;未注册 service 直接 fail closed)
        from app.services.operation_registry import uri_regex_for_service
        labels = {"service": service_ref, "uri": uri_regex_for_service(service_ref),
                  "extra": "", "window": window}
        p95_rows = self.query("HTTP_SERVER_P95_V1", labels, 300)
        qps_rows = self.query("HTTP_SERVER_QPS_V1", labels, 300)
        try:
            err_rows = self.query("HTTP_SERVER_ERROR_RATE_V1", labels, 300)
        except ValueError as e:
            if str(e) != ERROR_METRICS_NOT_FOUND:
                raise
            err_rows = []  # 无 5xx 请求 → 错误率视为 0(空集合法)
        latest = self._latest_sample_time(p95_rows)
        if evaluated_at - latest > settings.metrics_max_age_seconds:
            raise ValueError(ERROR_METRICS_STALE)
        try:
            p95 = float(p95_rows[0].get("value", [0, 0])[1]) * 1000.0
            qps = float(qps_rows[0].get("value", [0, 0])[1])
            err = float(err_rows[0].get("value", [0, 0])[1]) if err_rows else 0.0
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
