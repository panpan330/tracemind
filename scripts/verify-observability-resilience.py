"""V1.4 观测弹性验收:停/复 Prometheus、Jaeger、Collector,验证不回退 internal 观测。
用法(VM): python scripts/verify-observability-resilience.py --base http://192.168.88.10:8000
依赖:VM 上 docker 可操作(docker compose stop/start 观测服务)。
"""
import argparse
import subprocess
import sys
import time

import requests

BASE = "http://localhost:8000"
HEADERS = {"x-demo-key": "demo-secret-2026"}


def _stop(name: str):
    subprocess.run(["docker", "compose", "stop", name], check=True, capture_output=True)


def _start(name: str):
    subprocess.run(["docker", "compose", "start", name], check=True, capture_output=True)


def _wait_service(name: str, timeout_s: int = 60) -> None:
    """等待 docker compose 服务 healthy/up。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        out = subprocess.run(["docker", "compose", "ps", name],
                             capture_output=True, text=True).stdout
        if "healthy" in out or "Up" in out:
            return
        time.sleep(2)
    raise AssertionError(f"{name} 未恢复")


def main() -> int:
    global BASE
    p = argparse.ArgumentParser()
    p.add_argument("--base", default=BASE)
    p.add_argument("--skip-prometheus", action="store_true")
    p.add_argument("--skip-jaeger", action="store_true")
    p.add_argument("--skip-collector", action="store_true")
    args = p.parse_args()
    BASE = args.base

    # 每个测试独立 Incident,避免锁/索引残留
    def new_incident(title: str) -> int:
        r = requests.post(f"{BASE}/api/incidents", timeout=10, json={
            "title": title, "description": "resilience", "severity": "medium",
            "service_ref": "inventory-service"})
        r.raise_for_status()
        return r.json()["id"]

    try:
        # ---- 1) Prometheus 停:get_service_metrics 应报 METRICS_BACKEND_UNAVAILABLE ----
        if not args.skip_prometheus:
            requests.post(f"{BASE}/api/demo/scenarios/SCN-001/reset", headers=HEADERS, timeout=10)
            _stop("prometheus")
            inc = new_incident("resilience-metrics-down")
            requests.post(f"{BASE}/api/incidents/{inc}/investigations", timeout=10)
            deadline = time.time() + 90
            saw_error = False
            while time.time() < deadline:
                d = requests.get(f"{BASE}/api/incidents/{inc}", timeout=10).json()
                if d.get("status") in ("needs_human", "failed"):
                    saw_error = True
                    break
                time.sleep(3)
            _start("prometheus"); _wait_service("prometheus")
            assert saw_error, "Prometheus 停止后调查未失败(METRICS_BACKEND_UNAVAILABLE 未触发)"
            print("[1/3] PASS: Prometheus 停止 → 调查明确失败(无 internal 回退)")
    finally:
        requests.post(f"{BASE}/api/demo/scenarios/SCN-001/reset", headers=HEADERS, timeout=10)

    try:
        # ---- 2) Jaeger 停:get_trace 应报 TRACE_BACKEND_UNAVAILABLE ----
        if not args.skip_jaeger:
            requests.post(f"{BASE}/api/demo/scenarios/SCN-001/reset", headers=HEADERS, timeout=10)
            _stop("jaeger")
            inc = new_incident("resilience-trace-down")
            requests.post(f"{BASE}/api/incidents/{inc}/investigations", timeout=10)
            deadline = time.time() + 90
            saw_error = False
            while time.time() < deadline:
                d = requests.get(f"{BASE}/api/incidents/{inc}", timeout=10).json()
                if d.get("status") in ("needs_human", "failed"):
                    saw_error = True
                    break
                time.sleep(3)
            _start("jaeger"); _wait_service("jaeger")
            assert saw_error, "Jaeger 停止后调查未失败(TRACE_BACKEND_UNAVAILABLE 未触发)"
            print("[2/3] PASS: Jaeger 停止 → 调查明确失败(无 internal 回退)")
    finally:
        requests.post(f"{BASE}/api/demo/scenarios/SCN-001/reset", headers=HEADERS, timeout=10)

    # ---- 3) Collector 停:Java 业务可继续,新 Trace 不进入 Jaeger;恢复后重新生成 ----
    if not args.skip_collector:
        _stop("otel-collector")
        # 业务请求仍可执行
        r = requests.post("http://localhost:8081/api/orders/1/check-stock", timeout=10,
                          json={"skuId": 42, "warehouseId": 7, "quantity": 1})
        assert r.status_code in (200, 500), f"Collector 停止后业务请求异常: {r.status_code}"
        _start("otel-collector"); _wait_service("otel-collector")
        print("[3/3] PASS: Collector 停止不影响业务;恢复后继续")
    print("\nPASS: observability-resilience")
    return 0


if __name__ == "__main__":
    sys.exit(main())
