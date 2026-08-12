from app.services import metrics_service, trace_service


def test_metrics_fixture_backend(monkeypatch):
    monkeypatch.setattr("app.config.settings.metrics_backend", "fixture")
    out = metrics_service.get_metrics("inventory-service",
                                      "2026-08-12T00:00:00Z", "2026-08-12T00:05:00Z")
    assert out["sourceBackend"] == "fixture"


def test_metrics_prometheus_backend(monkeypatch):
    monkeypatch.setattr("app.config.settings.metrics_backend", "prometheus")
    captured = {}

    class FakeClient:
        def __init__(self, base_url=None):
            pass

        def get_service_metrics(self, service, ws, we):
            captured["ok"] = True
            return {"sourceBackend": "prometheus", "p95Ms": 100}

    monkeypatch.setattr("app.services.metrics_service.PrometheusMetricsClient", FakeClient)
    out = metrics_service.get_metrics("inventory-service", "a", "b")
    assert captured["ok"] and out["p95Ms"] == 100


def test_trace_fixture_backend(monkeypatch):
    monkeypatch.setattr("app.config.settings.trace_backend", "fixture")
    out = trace_service.get_trace(trace_ref="REPRESENTATIVE_SLOW_TRACE", trace_id=None,
                                  incident={"id": 1, "affected_service_ref": "inventory-service",
                                            "affected_operation_ref": "INVENTORY_LOOKUP"})
    assert out["sourceBackend"] == "fixture"


def test_trace_jaeger_backend_maps_ref(monkeypatch):
    monkeypatch.setattr("app.config.settings.trace_backend", "jaeger")
    calls = {}

    class FakeClient:
        def search_traces(self, svc, op, s, e, strat):
            calls.update(svc=svc, op=op, strat=strat)
            return [{"traceID": "t1"}]

        def get_trace_by_id(self, tid):
            return {"traceID": tid, "spans": []}

    class FakeNorm:
        def normalize(self, trace, op):
            return {"status": "ok", "dbDominanceRatio": 0.9,
                    "normalizationRuleVersion": "TRACE_NORMALIZER_V1"}

    monkeypatch.setattr("app.services.trace_service.JaegerTraceClient", FakeClient)
    monkeypatch.setattr("app.services.trace_service.TraceNormalizer", FakeNorm)
    out = trace_service.get_trace(trace_ref="REPRESENTATIVE_SLOW_TRACE", trace_id=None,
                                  incident={"id": 1, "affected_service_ref": "inventory-service",
                                            "affected_operation_ref": "INVENTORY_RESERVATION"})
    assert calls["svc"] == "inventory-service"
    assert calls["op"] == "INVENTORY_RESERVATION"
    assert calls["strat"] == "SLOWEST"
    assert out["sourceBackend"] == "jaeger" and out["traceId"] == "t1"


def test_metrics_records_audit_without_raw(monkeypatch):
    records = []

    def fake_record(**kw):
        records.append(kw)
        assert "normalized_result" in kw  # 仅归一化结果,不含原始响应

    monkeypatch.setattr("app.services.metrics_service.observation_repo.record_query", fake_record)
    monkeypatch.setattr("app.config.settings.metrics_backend", "fixture")
    out = metrics_service.get_metrics("inventory-service", "a", "b",
                                      incident_id=7, agent_run_id=8)
    assert records and records[0]["incident_id"] == 7
    assert records[0]["backend"] == "fixture"
    assert out["p95Ms"] == 2
