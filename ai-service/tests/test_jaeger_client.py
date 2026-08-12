from app.services.jaeger_client import JaegerTraceClient


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        self.calls.append((url, params))
        return self.responses.pop(0)


def test_search_uses_whitelisted_bounds(monkeypatch):
    fake = FakeClient([FakeResponse({"data": []})])
    monkeypatch.setattr("app.services.jaeger_client.httpx.Client", lambda *a, **k: fake)
    c = JaegerTraceClient(endpoint="jaeger:16685")
    out = c.search_traces("inventory-service", "INVENTORY_RESERVATION",
                          "2026-08-12T00:00:00Z", "2026-08-12T00:05:00Z", "SLOWEST")
    assert out == []
    url, params = fake.calls[0]
    assert "/api/traces" in url
    assert params["service"] == "inventory-service"
    assert params["limit"] <= 20


def test_get_trace_by_id_not_found(monkeypatch):
    fake = FakeClient([FakeResponse({"data": []})])
    monkeypatch.setattr("app.services.jaeger_client.httpx.Client", lambda *a, **k: fake)
    c = JaegerTraceClient(endpoint="jaeger:16685")
    try:
        c.get_trace_by_id("nonexistent")
        raise AssertionError("应抛出 TRACE_NOT_FOUND")
    except ValueError as e:
        assert "TRACE_NOT_FOUND" in str(e)
