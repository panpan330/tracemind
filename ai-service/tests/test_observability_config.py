"""observability 配置结构测试(YAML/JSON 解析与关键键断言)。"""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_collector_trace_only_pipeline():
    cfg = yaml.safe_load((ROOT / "observability" / "otel-collector.yaml").read_text(encoding="utf-8"))
    svc = cfg["service"]["pipelines"]
    assert "traces" in svc and "metrics" not in svc and "logs" not in svc
    assert svc["traces"]["receivers"][0] == "otlp"
    assert svc["traces"]["exporters"][0] == "otlp"


def test_prometheus_scrapes_management_ports():
    cfg = yaml.safe_load((ROOT / "observability" / "prometheus.yml").read_text(encoding="utf-8"))
    targets = cfg["scrape_configs"][0]["static_configs"][0]["targets"]
    assert "order-service:9081" in targets and "inventory-service:9082" in targets


def test_grafana_provisioning_committed():
    ds = ROOT / "observability" / "grafana" / "provisioning" / "datasources" / "prometheus.yml"
    dash = ROOT / "observability" / "grafana" / "dashboards" / "tracemind-overview.json"
    assert ds.exists() and dash.exists()
    data = json.loads(dash.read_text(encoding="utf-8"))
    assert data["title"] == "TraceMind Overview"
    assert len(data["panels"]) >= 3
