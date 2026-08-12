from app.services.jaeger_client import JaegerTraceClient


def test_search_uses_whitelisted_bounds(monkeypatch):
    captured = {}

    def fake_query(endpoint, request):
        captured["req"] = request
        return {"traces": []}

    monkeypatch.setattr("app.services.jaeger_client._query_grpc", fake_query)
    c = JaegerTraceClient(endpoint="jaeger:16685")
    out = c.search_traces("inventory-service", "INVENTORY_RESERVATION",
                          "2026-08-12T00:00:00Z", "2026-08-12T00:05:00Z", "SLOWEST")
    assert out == []
    assert captured["req"]["service"] == "inventory-service"
    assert captured["req"]["operation"] == "INVENTORY_RESERVATION"
    assert captured["req"]["limit"] <= 20


def test_get_trace_by_id_not_found(monkeypatch):
    def fake_query(endpoint, request):
        return {"trace": None}

    monkeypatch.setattr("app.services.jaeger_client._query_grpc", fake_query)
    c = JaegerTraceClient(endpoint="jaeger:16685")
    try:
        c.get_trace_by_id("nonexistent")
        raise AssertionError("应抛出 TRACE_NOT_FOUND")
    except ValueError as e:
        assert "TRACE_NOT_FOUND" in str(e)
