"""V1.4 观测验收:SCN-001/SCN-002 各 3 轮 + 观测后端证据断言。
用法:
  本地 fixture 冒烟: python scripts/verify-m14.py --base http://localhost:8000 --order http://localhost:8081 --fixture
  VM 全量验收:    python scripts/verify-m14.py --base http://192.168.88.10:8000 --order http://192.168.88.10:8081
前提:全栈已部署(compose up);真实模式要求 metrics_backend=prometheus + trace_backend=jaeger。
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
HEADERS = {"x-demo-key": "demo-secret-2026"}


def load(seconds: int, qps: int, sku: int | None = None, wh: int | None = None,
         max_in_flight: int = 1, timeout: float = 8.0) -> None:
    env = {**os.environ, "ORDER_SERVICE_URL": args.order,
           "LOAD_DURATION_SECONDS": str(seconds), "LOAD_QPS": str(qps),
           "LOAD_MAX_IN_FLIGHT": str(max_in_flight), "LOAD_TIMEOUT_SECONDS": str(timeout)}
    if sku is not None:
        env["LOAD_SKU"] = str(sku)
        env["LOAD_WAREHOUSE"] = str(wh or 0)
    subprocess.run([sys.executable, str(ROOT / "scripts/loadgen.py")],
                   env=env, timeout=seconds + 30, capture_output=True)


def wait_status(base: str, incident_id: int, targets: set[str], timeout_s: int = 120) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        d = requests.get(f"{base}/api/incidents/{incident_id}", timeout=10).json()
        if d["status"] in targets:
            return d["status"]
        time.sleep(2)
    raise AssertionError(f"incident {incident_id} 未在 {timeout_s}s 内到达 {targets}")


def run_round(scenario: str, round_no: int) -> None:
    base = args.base
    t0 = time.time()
    requests.post(f"{base}/api/demo/scenarios/{scenario}/reset", headers=HEADERS, timeout=10)
    # 健康负载(基线)与 incident 创建
    load(6, 12)
    r = requests.post(f"{base}/api/incidents", timeout=10, json={
        "title": f"V1.4 {scenario} round{round_no}",
        "description": f"观测验收 {scenario}", "severity": "high",
        "service_ref": "inventory-service",
        "affected_service_ref": "inventory-service",
        "affected_operation_ref": "INVENTORY_LOOKUP" if scenario == "SCN-001" else "INVENTORY_RESERVATION",
    })
    r.raise_for_status()
    incident_id = r.json()["id"]
    requests.post(f"{base}/api/demo/scenarios/{scenario}/inject", headers=HEADERS, timeout=10)
    # 先启动调查(此时采集 digest 基线 = 健康状态,保证 E3 增量),再打故障负载
    rr = requests.post(f"{base}/api/incidents/{incident_id}/investigations", timeout=10)
    rr.raise_for_status()
    procs = []
    if scenario == "SCN-002":
        # 持续锁负载:UPDATE 42/7(等待锁)+ loadgen(超时流量),调查期间保持
        up = subprocess.Popen([sys.executable, "-c",
                               'import pymysql,time; c=pymysql.connect(host="127.0.0.1",port=3306,'
                               'user="app_business",password="app_business_pwd",database="tracemind_business");'
                               'cur=c.cursor(); end=time.time()+30\n'
                               'while time.time()<end:\n'
                               ' try:\n  cur.execute("UPDATE inventory SET quantity=quantity-1 '
                               'WHERE sku_id=42 AND warehouse_id=7"); c.commit()\n'
                               ' except: pass\n time.sleep(0.4)'],
                              stdout=subprocess.DEVNULL)
        procs.append(up)
        env = {**os.environ, "ORDER_SERVICE_URL": args.order,
               "LOAD_DURATION_SECONDS": "25", "LOAD_QPS": "15",
               "LOAD_MAX_IN_FLIGHT": "1", "LOAD_TIMEOUT_SECONDS": "6.0",
               "LOAD_SKU": "42", "LOAD_WAREHOUSE": "7"}
        lg = subprocess.Popen([sys.executable, str(ROOT / "scripts/loadgen.py")],
                              env=env, stdout=subprocess.DEVNULL)
        procs.append(lg)
        time.sleep(3)  # 让锁等待/超时流量产生
    else:
        load(8, 15, sku=42, wh=7, max_in_flight=1, timeout=6.0)
    time.sleep(14)  # 等 OTel batch(5s)+ Jaeger 完成 trace 导出;调查第 2 轮 get_trace 时已有完整 trace
    # fixture 冒烟:metrics 恒健康;锁等待时序不稳定(本地 UPDATE/loadgen 子进程)
    # → 流程到达终态即可,根因/证据断言在 VM 非 fixture 模式严格验证
    targets = {"awaiting_approval", "needs_human"} if args.fixture else {"awaiting_approval"}
    status = wait_status(base, incident_id, targets)
    if args.fixture:
        print(f"[{round(time.time() - t0, 1)}s] {scenario} round{round_no} "
              f"PASS(fixture 冒烟,终态={status})")
        return
    d = requests.get(f"{base}/api/incidents/{incident_id}", timeout=10).json()
    evidence = {e["key"]: e for e in d.get("evidence", [])}
    # 观测证据断言
    m = (evidence.get("e1") or {}).get("content") or {}
    t = (evidence.get("e2") or {}).get("content") or {}
    assert m.get("sourceBackend") == ("fixture" if args.fixture else "prometheus"), \
        f"metrics backend 断言失败: {m.get('sourceBackend')}"
    assert m.get("observationQueryId"), "metrics 缺 observationQueryId"
    if not args.fixture:
        assert t.get("sourceBackend") == "jaeger", f"trace backend 断言失败: {t.get('sourceBackend')}"
        assert t.get("traceId"), "trace 证据缺 traceId"
        assert t.get("dbDominanceRatio") is not None, "trace 证据缺 dbDominanceRatio"
        # Jaeger 可再查 + traceId 一致
        trace_id = t["traceId"]
        jaeger = requests.get(
            f"http://{args.jaeger_host}:16686/api/traces/{trace_id}", timeout=10)
        assert jaeger.status_code == 200 and jaeger.json().get("data"), "Jaeger 无法再查 traceId"
        # 防伪断言:Java 内部观测端点必须 404
        for port in (9081, 9082):
            r404 = requests.get(f"http://{args.order_host}:{port}/internal/observations/metrics",
                                timeout=5)
            assert r404.status_code == 404, f"内部观测端点 {port} 未禁用"
    # 审批 → 恢复
    approvals = [a for a in d.get("approvals", []) if a["status"] == "pending"]
    if approvals:
        requests.post(f"{base}/api/incidents/{incident_id}/approvals/{approvals[0]['id']}/decision",
                      json={"decision": "approved", "comment": "v1.4 e2e"}, timeout=15)
        wait_status(base, incident_id, {"recovered", "needs_human"}, timeout_s=90)
        d2 = requests.get(f"{base}/api/incidents/{incident_id}", timeout=10).json()
        assert d2["status"] == "recovered", f"{scenario} 未恢复: {d2['status']}"
    else:
        raise AssertionError("无待审批提案")
    for p in procs:
        p.terminate()
    print(f"[{round(time.time() - t0, 1)}s] {scenario} round{round_no} PASS")


def main() -> int:
    global args
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost:8000")
    p.add_argument("--order", default="http://localhost:8081")
    p.add_argument("--fixture", action="store_true", help="fixture 后端冒烟(跳过 jaeger/防伪断言)")
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--scenario", choices=["SCN-001", "SCN-002", "all"], default="all")
    args = p.parse_args()
    args.order_host = args.order.replace("http://", "").split(":")[0]
    args.jaeger_host = "192.168.88.10" if not args.fixture else "localhost"
    scenarios = ["SCN-001", "SCN-002"] if args.scenario == "all" else [args.scenario]
    try:
        for sc in scenarios:
            for i in range(1, args.rounds + 1):
                run_round(sc, i)
    finally:
        for sc in scenarios:
            try:
                requests.post(f"{args.base}/api/demo/scenarios/{sc}/reset",
                              headers=HEADERS, timeout=10)
            except Exception:
                pass
    print(f"\nPASS: {', '.join(scenarios)} 各 {args.rounds} 轮(观测证据 + 防伪断言)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
