import pytest
from app.services.prometheus_client import PrometheusMetricsClient
from app.services import promql_templates


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, data=None, **kw):
        self.calls.append((url, data))
        return FakeResponse(self.responses.pop(0))


def test_templates_registered():
    assert "HTTP_SERVER_P95_V1" in promql_templates.TEMPLATES
    assert "HTTP_SERVER_QPS_V1" in promql_templates.TEMPLATES
    assert "HTTP_SERVER_ERROR_RATE_V1" in promql_templates.TEMPLATES


def test_get_service_metrics_parses_instant_vector(monkeypatch):
    import time
    now = time.time()
    fake = FakeClient([
        {"status": "success", "data": {"resultType": "vector",
         "result": [{"metric": {"le": "+Inf"}, "value": [now, "0.42"]}]}},
        {"status": "success", "data": {"resultType": "vector",
         "result": [{"metric": {}, "value": [1700000000.0, "12.5"]}]}},
        {"status": "success", "data": {"resultType": "vector",
         "result": [{"metric": {}, "value": [1700000000.0, "0.08"]}]}},
    ])
    monkeypatch.setattr("app.services.prometheus_client.httpx.Client", lambda *a, **k: fake)
    c = PrometheusMetricsClient(base_url="http://prom:9090")
    out = c.get_service_metrics("inventory-service", "2026-08-12T00:00:00Z", "2026-08-12T00:05:00Z")
    assert out["p95Ms"] == 420 and out["qps"] == 12.5 and out["errorRate"] == 0.08
    assert out["sourceBackend"] == "prometheus"
    assert out["queryTemplateId"] == "HTTP_SERVER_P95_V1"
    assert len(fake.calls) == 3  # P95/QPS/错误率各一次,不接收 LLM 生成的查询文本


def test_stale_detection(monkeypatch):
    import time
    old = time.time() - 500
    fake = FakeClient([
        {"status": "success", "data": {"resultType": "vector",
         "result": [{"metric": {"le": "+Inf"}, "value": [old, "0.42"]}]}},
        {"status": "success", "data": {"resultType": "vector",
         "result": [{"metric": {}, "value": [old, "12.5"]}]}},
        {"status": "success", "data": {"resultType": "vector",
         "result": [{"metric": {}, "value": [old, "0.08"]}]}},
    ])
    monkeypatch.setattr("app.services.prometheus_client.httpx.Client", lambda *a, **k: fake)
    c = PrometheusMetricsClient(base_url="http://prom:9090")
    with pytest.raises(ValueError) as ei:
        c.get_service_metrics("inventory-service", "2026-08-12T00:00:00Z", "2026-08-12T00:05:00Z")
    assert "METRICS_STALE" in str(ei.value)
