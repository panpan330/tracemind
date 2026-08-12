from app.services.trace_normalizer import TraceNormalizer


def _span(span_id, kind, service, name, start, dur, parent=None, attrs=None):
    return {"spanId": span_id, "kind": kind, "process": {"serviceName": service},
            "operationName": name, "startTime": start, "duration": dur,
            "parentSpanId": parent, "tags": attrs or {}}


def test_normalize_full_chain():
    trace = {"traceID": "abc", "spans": [
        _span("s1", "SPAN_KIND_SERVER", "inventory-service", "GET /api/inventory",
              1000, 900000, None, {"http.route": "/api/inventory"}),
        _span("s2", "SPAN_KIND_INTERNAL", "inventory-service", "inventory.lookup",
              1100, 850000, "s1", {}),
        _span("s3", "SPAN_KIND_CLIENT", "inventory-service", "SELECT inventory",
              1200, 820000, "s2", {"db.system.name": "mysql",
                                   "db.operation.name": "SELECT"}),
    ]}
    out = TraceNormalizer().normalize(trace, "INVENTORY_LOOKUP")
    assert out["inventoryServerDurationMs"] == 900
    assert out["targetDbDurationMs"] == 820
    assert out["dbDominanceRatio"] > 0.9
    assert out["targetDbSpanId"] == "s3"
    assert out["normalizationRuleVersion"] == "TRACE_NORMALIZER_V1"


def test_normalize_ignores_management_spans():
    trace = {"traceID": "def", "spans": [
        _span("s1", "SPAN_KIND_SERVER", "inventory-service", "GET /api/inventory",
              1000, 900000, None, {"http.route": "/api/inventory"}),
        _span("s2", "SPAN_KIND_SERVER", "inventory-service", "GET /internal/scenarios/inject",
              2000, 10000, None, {"http.route": "/internal/scenarios/inject"}),
        _span("s3", "SPAN_KIND_CLIENT", "inventory-service", "SELECT inventory",
              1200, 820000, "s1", {"db.system.name": "mysql",
                                   "db.operation.name": "SELECT"}),
    ]}
    out = TraceNormalizer().normalize(trace, "INVENTORY_LOOKUP")
    # 管理 span 被忽略,业务 SERVER span(s1)被选中
    assert out["inventoryServerDurationMs"] == 900
    assert out["targetDbSpanId"] == "s3"


def test_normalize_legacy_semconv_fallback():
    """旧字段 db.system/db.operation 兼容映射(Fixture/迁移期)。"""
    trace = {"traceID": "jkl", "spans": [
        _span("s1", "SPAN_KIND_SERVER", "inventory-service", "GET /api/inventory",
              1000, 900000, None, {"http.route": "/api/inventory"}),
        _span("s3", "SPAN_KIND_CLIENT", "inventory-service", "SELECT inventory",
              1200, 820000, "s1", {"db.system": "mysql", "db.operation": "SELECT"}),
    ]}
    out = TraceNormalizer().normalize(trace, "INVENTORY_LOOKUP")
    assert out["targetDbSpanId"] == "s3"


def test_normalize_incomplete_trace_returns_incomplete():
    trace = {"traceID": "ghi", "spans": [
        _span("s1", "SPAN_KIND_SERVER", "inventory-service", "GET /api/inventory",
              1000, 900000, None, {"http.route": "/api/inventory"}),
    ]}  # 无 DB CLIENT span
    out = TraceNormalizer().normalize(trace, "INVENTORY_LOOKUP")
    assert out.get("status") == "TRACE_INCOMPLETE"
