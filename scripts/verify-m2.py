"""M2 验收:不调用 LLM,通过七工具手动取齐 E1~E5 证据。

前置:两个 Java 服务已启动(inventory 需 DEMO_MODE=true);AI 服务已启动。
用法: python scripts/verify-m2.py
环境变量: AI_SERVICE_URL / INVENTORY_SERVICE_URL / DEMO_KEY
"""
import os
import subprocess
import sys

import httpx

AI = os.environ.get("AI_SERVICE_URL", "http://localhost:8000")
DEMO_KEY = os.environ.get("DEMO_KEY", "demo-secret-2026")
SKU, WH = 42, 7
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_load(seconds: int = 8, qps: int = 10) -> None:
    """跑一小段负载,让 Java 观测与 performance_schema 产生数据。"""
    env = {**os.environ, "LOAD_DURATION_SECONDS": str(seconds), "LOAD_QPS": str(qps)}
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "loadgen.py")],
                   env=env, cwd=ROOT, check=True)


def main() -> int:
    c = httpx.Client(base_url=AI, timeout=15)
    # 0) 重置场景并注入故障
    c.post("/api/demo/scenarios/SCN-001/reset", headers={"x-demo-key": DEMO_KEY})
    c.post("/api/demo/scenarios/SCN-001/inject", headers={"x-demo-key": DEMO_KEY})

    # 1) 创建 Incident(先采集 digest 基线),随后制造故障态负载
    inc = c.post("/api/incidents", json={
        "title": "M2 验收:库存查询变慢", "severity": "high",
        "service_ref": "inventory-service"}).json()
    iid = inc["id"]
    run_load(seconds=8, qps=10)
    run = c.post(f"/api/incidents/{iid}/investigations").json()
    print(f"incident_id={iid} run_id={run['run_id']}")

    def call(tool: str, **args) -> dict:
        return c.post(f"/api/incidents/{iid}/tools",
                      json={"tool": tool, "args": args}).json()

    # 2) 工具调用,取齐 E1~E5
    e1 = call("get_service_metrics", service_ref="inventory-service", window_seconds=300)
    trace_id = e1.get("data", {}).get("representativeSlowTraceId")
    e2 = call("get_trace", trace_id=trace_id) if trace_id else {"success": False,
                                                                "data": "no trace"}
    e3 = call("list_expensive_query_digests", incident_id=iid)
    e4 = call("get_query_plan", query_ref="INVENTORY_LOOKUP",
              sample_parameters={"skuId": SKU, "warehouseId": WH})
    e5 = call("get_index_info", table_ref="inventory")

    print("\n=== E1 get_service_metrics ===")
    print("success:", e1["success"], "| p95Ms:", e1.get("data", {}).get("p95Ms"),
          "| slow_trace:", trace_id)
    print("=== E2 get_trace ===")
    print("success:", e2["success"])
    if e2.get("success"):
        print("  inventory_service stages:", [r["stage"] for r in e2["data"]["inventory_service"]])
    print("=== E3 list_expensive_query_digests ===")
    print("success:", e3["success"])
    for d in (e3.get("data") or [])[:3]:
        print(f"  rows_examined_delta={d['rows_examined_delta']} count_delta={d['count_delta']}")
    print("=== E4 get_query_plan ===")
    print("success:", e4["success"])
    if e4.get("success"):
        table = e4["data"]["explain"]["query_block"]["table"]
        print("  access_type:", table.get("access_type"), "| rows:", table.get("rows"),
              "| possible_keys:", table.get("possible_keys"))
    print("=== E5 get_index_info ===")
    print("success:", e5["success"])
    names = [i["index_name"] for i in (e5.get("data", {}).get("indexes") or [])]
    print("  indexes:", names, "| idx_sku_warehouse present:", "idx_sku_warehouse" in names)

    ok = all(x["success"] for x in (e1, e2, e3, e4, e5))
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
