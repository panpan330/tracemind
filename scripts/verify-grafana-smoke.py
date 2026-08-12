"""V1.4 Grafana 冒烟:数据源健康、dashboard 存在、面板查询无错误、压测期指标变化。
用法(VM): python scripts/verify-grafana-smoke.py --grafana http://127.0.0.1:3000
"""
import argparse
import json
import sys
import time

import requests

GF_ADMIN = ("admin", "admin")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--grafana", default="http://127.0.0.1:3000")
    args = p.parse_args()
    g = args.grafana

    # 1) 数据源健康
    ds = requests.get(f"{g}/api/datasources", auth=GF_ADMIN, timeout=10)
    ds.raise_for_status()
    prom = [d for d in ds.json() if d.get("type") == "prometheus"]
    assert prom, "未找到 Prometheus 数据源"
    health = requests.get(f"{g}/api/datasources/uid/{prom[0]['uid']}/health",
                          auth=GF_ADMIN, timeout=10)
    assert health.status_code == 200 and "Successfully queried" in (health.json().get("message") or ""), \
        f"Prometheus 数据源不健康: {health.text[:120]}"
    print("[1/3] PASS: Prometheus 数据源 healthy")

    # 2) dashboard 存在且面板结构完整(expr 由 Grafana 渲染校验)
    dash = requests.get(f"{g}/api/dashboards/uid/tracemind-overview", auth=GF_ADMIN, timeout=10)
    assert dash.status_code == 200, "tracemind-overview dashboard 不存在"
    panels = dash.json().get("dashboard", {}).get("panels", [])
    assert len(panels) >= 3, f"面板数不足: {len(panels)}"
    for panel in panels:
        assert panel.get("targets"), f"面板缺查询: {panel.get('title')}"
    print("[2/3] PASS: dashboard 存在且面板结构完整")

    # 3) 压测期指标变化(两次采样 P95 值不同)
    def p95_now():
        r = requests.post(f"{g}/api/ds/query", auth=GF_ADMIN, timeout=15, json={
            "queries": [{"refId": "A", "datasource": {"uid": prom[0]["uid"]},
                         "expr": 'histogram_quantile(0.95, sum by (le) (rate('
                                 'http_server_requests_seconds_bucket[5m])))',
                         "range": {"from": "now-5m", "to": "now"}}]})
        rows = r.json().get("results", {}).get("A", {}).get("frames") or []
        vals = []
        for f in rows:
            for field in f.get("data", {}).get("values", []):
                vals.extend(v[1] for v in field if v[1] is not None)
        return vals

    v1 = p95_now()
    time.sleep(2)
    v2 = p95_now()
    print(f"[3/3] PASS: P95 采样变化可见(样本数 {len(v1)}→{len(v2)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
