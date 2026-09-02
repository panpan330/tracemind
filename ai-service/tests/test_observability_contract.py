"""V2.0-A:可观测性契约测试。

静态部分:uri 模板与 Java 控制器逐项对照(基准事实,变更需两侧同步);
动态部分:仅在真实 Prometheus 可达时运行冒烟,失败输出实际 label 值,
禁止用通配符自动放宽(升级方案 V2.0 实施内容第 2 条)。
"""
import urllib.parse
import urllib.request

import pytest

from app.services.operation_registry import (OPERATION_TO_ROUTE, OPERATION_TO_URI,
                                             SERVICE_TO_OPERATIONS,
                                             uri_regex_for_service,
                                             uri_templates_for_service)

# ---- 基准事实:与 java/ 控制器映射逐项对照(冻结契约) ----
# order-service: @RequestMapping("/api/orders") + @PostMapping("/{orderId}/check-stock")
# inventory-service: @RequestMapping("/api/inventory") + @GetMapping
EXPECTED_URI_TEMPLATES = {
    "ORDER_CREATE": "/api/orders/{orderId}/check-stock",
    "INVENTORY_LOOKUP": "/api/inventory",
    "INVENTORY_RESERVATION": "/api/inventory",
}


def test_uri_templates_match_java_controllers():
    assert OPERATION_TO_URI == EXPECTED_URI_TEMPLATES
    assert OPERATION_TO_ROUTE == EXPECTED_URI_TEMPLATES


def test_service_operation_coverage_complete():
    assert set(SERVICE_TO_OPERATIONS) == {"order-service", "inventory-service"}
    for svc, ops in SERVICE_TO_OPERATIONS.items():
        for op in ops:
            assert op in EXPECTED_URI_TEMPLATES


def test_uri_regex_is_anchored_and_escaped():
    rx = uri_regex_for_service("order-service")
    assert rx.startswith("^(") and rx.endswith(")$")   # 锚定,不是子串匹配
    assert "{" not in rx.replace("\\{", "")             # 字面花括号已转义
    assert ".+" not in rx                               # 禁止通配
    import re
    assert re.match(rx, "/api/orders/{orderId}/check-stock")
    assert not re.match(rx, "/api/orders/1/check-stock")     # 模板路径,不是具体 ID 路径
    assert not re.match(rx, "/internal/scenarios/SCN-001/inject")


def test_unknown_service_fails_closed():
    with pytest.raises(ValueError, match="METRICS_RESULT_INVALID"):
        uri_regex_for_service("mystery-service")
    with pytest.raises(ValueError, match="METRICS_RESULT_INVALID"):
        uri_templates_for_service("mystery-service")


def test_prometheus_query_uses_registry_not_wildcard(monkeypatch):
    """P95 查询的 uri 标签必须是 registry 正则(回退通配即回归)。"""
    import time

    from app.services import prometheus_client as pc

    captured = {}

    class FakeResp:
        def json(self):
            return {"status": "success", "data": {"resultType": "vector", "result": [
                {"metric": {"le": "+Inf"}, "value": [time.time(), "0.02"]}]}}

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, data=None, **kw):
            captured["query"] = data["query"]
            return FakeResp()

    monkeypatch.setattr(pc.httpx, "Client", FakeClient)
    c = pc.PrometheusMetricsClient(base_url="http://prom:9090")
    c.get_service_metrics("order-service", "w0", "w1")
    q = captured["query"]
    assert "\\{orderId\\}" in q                 # 模板路径花括号已转义(字面匹配)
    assert 'uri=~".+"' not in q                 # 禁止通配回归
    assert ')$"}' in q                          # 正则锚定结尾(registry 生成)


def _prometheus_reachable() -> bool:
    import os
    base = os.environ.get("TRACEMIND_PROMETHEUS_URL")
    if not base:
        return False
    try:
        with urllib.request.urlopen(f"{base}/-/ready", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


@pytest.mark.skipif(not _prometheus_reachable(), reason="真实 Prometheus 不可达(live 冒烟可选)")
def test_live_prometheus_label_contract():
    """真实后端契约冒烟:业务 uri 模板在 http_server_requests_seconds_count 序列中真实存在。
    失败时打印实际 label 值(禁止自动放宽为通配)。"""
    import json
    import os
    base = os.environ["TRACEMIND_PROMETHEUS_URL"]
    match = urllib.parse.quote('{__name__="http_server_requests_seconds_count"}')
    with urllib.request.urlopen(f"{base}/api/v1/series?match[]={match}", timeout=5) as r:
        body = json.load(r)
    assert body.get("status") == "success", body
    series = body["data"]
    assert series, "Prometheus 无 http_server_requests_seconds_count 序列(服务未抓取?)"
    uris = {s.get("uri") for s in series if s.get("uri")}
    expected = (uri_templates_for_service("order-service")
                + uri_templates_for_service("inventory-service"))
    missing = [t for t in expected if t not in uris]
    assert not missing, f"Prometheus 实际 uri 标签缺 {missing};实际值样本: {sorted(uris)[:20]}"
