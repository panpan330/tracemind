"""M5 端到端验收(Docker Compose 部署后):注入 → 调查 → 审批 → 执行 → recovered → 报告。

前置:docker compose 全部 healthy;AI 服务 :8000、order :8081 可从本机访问。
用法:
  python scripts/verify-m5.py                                # localhost 全栈
  python scripts/verify-m5.py --base http://192.168.88.10:8000 --order http://192.168.88.10:8081
"""
import argparse
import os
import subprocess
import sys
import time

import requests

DEMO_KEY = "demo-secret-2026"
HEADERS = {"x-demo-key": DEMO_KEY}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def wait_status(ai: str, incident_id: str, target: str, timeout: int = 90) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        inc = requests.get(f"{ai}/api/incidents/{incident_id}").json()
        if inc["status"] == target:
            return inc
        time.sleep(2)
    fail(f"incident {incident_id} 未在 {timeout}s 内到达 {target},当前 {inc.get('status')}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("TRACEMIND_AI_BASE", "http://localhost:8000"))
    ap.add_argument("--order", default=os.environ.get("ORDER_SERVICE_URL", "http://localhost:8081"))
    args = ap.parse_args()
    ai = args.base.rstrip("/")
    t0 = time.time()

    def run_load(seconds: int = 8, qps: int = 15) -> None:
        env = {**os.environ, "ORDER_SERVICE_URL": args.order,
               "LOAD_DURATION_SECONDS": str(seconds), "LOAD_QPS": str(qps)}
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "loadgen.py")],
                       env=env, cwd=ROOT, check=True, timeout=90)

    # 健康检查
    r = requests.get(f"{ai}/api/health", timeout=10)
    if r.status_code != 200:
        fail(f"AI 服务不可达: {ai} -> {r.status_code}")

    # 1) 重置环境(健康态)
    r = requests.post(f"{ai}/api/demo/scenarios/SCN-001/reset", headers=HEADERS, timeout=15)
    if r.status_code >= 400:
        fail(f"reset 失败: {r.status_code} {r.text[:200]}")
    print(f"[{time.time()-t0:5.1f}s] reset 完成")

    # 2) 健康态创建 Incident(采集健康指标基线)
    r = requests.post(f"{ai}/api/incidents", json={
        "title": "M5 验收:库存查询变慢",
        "description": "Docker Compose 部署闭环",
        "severity": "high",
        "service_ref": "inventory-service",
    }, timeout=15)
    if r.status_code != 201:
        fail(f"创建 incident 失败: {r.status_code} {r.text[:200]}")
    incident_id = str(r.json()["id"])
    print(f"[{time.time()-t0:5.1f}s] incident {incident_id} 已创建(健康基线已采集)")

    # 3) 注入故障
    r = requests.post(f"{ai}/api/demo/scenarios/SCN-001/inject", headers=HEADERS, timeout=15)
    if r.status_code >= 400:
        fail(f"inject 失败: {r.status_code} {r.text[:200]}")
    print(f"[{time.time()-t0:5.1f}s] 故障已注入")

    # 4) 启动调查(采集 digest 基线)
    r = requests.post(f"{ai}/api/incidents/{incident_id}/investigations", timeout=15)
    if r.status_code != 202:
        fail(f"启动调查失败: {r.status_code} {r.text[:200]}")
    run_id = r.json()["run_id"]
    print(f"[{time.time()-t0:5.1f}s] 调查已启动 run={run_id}")

    # 5) 故障态负载(digest 增量 + 故障 P95)
    run_load(seconds=8, qps=15)
    print(f"[{time.time()-t0:5.1f}s] 故障态负载完成")

    # 6) 轮询到 awaiting_approval,断言假设 + E1~E5 + pending 审批
    inc = wait_status(ai, incident_id, "awaiting_approval")
    print(f"[{time.time()-t0:5.1f}s] 到达 awaiting_approval")
    hyps = inc.get("hypotheses") or []
    if not any("缺少联合索引" in (h.get("description") or "") for h in hyps):
        fail(f"假设缺失: {hyps}")
    passed_keys = {e["key"] for e in (inc.get("evidence") or []) if e.get("passed")}
    for k in ("E1", "E2", "E3", "E4", "E5"):
        if k not in passed_keys:
            fail(f"证据 {k} 未通过: passed={sorted(passed_keys)}")
    print(f"[{time.time()-t0:5.1f}s] 假设与 E1~E5 证据齐备")

    pending = [a for a in (inc.get("approvals") or []) if a.get("status") == "pending"]
    if not pending:
        fail(f"无 pending 审批: {inc.get('approvals')}")
    approval_id = pending[0]["id"]
    print(f"[{time.time()-t0:5.1f}s] 审批 {approval_id} pending")

    # 7) 批准 → recovered
    r = requests.post(
        f"{ai}/api/incidents/{incident_id}/approvals/{approval_id}/decision",
        json={"decision": "approved", "comment": "M5 验收批准"}, timeout=20)
    if r.status_code >= 400:
        fail(f"审批失败: {r.status_code} {r.text[:200]}")
    inc = wait_status(ai, incident_id, "recovered")
    print(f"[{time.time()-t0:5.1f}s] 已恢复(recovered)")

    # 8) 断言 fix/recovery/report
    fix_exec = inc.get("fix_execution") or {}
    if fix_exec.get("status") not in ("succeeded", "no_op"):
        fail(f"fix_execution 异常: {fix_exec}")
    recovery = inc.get("recovery") or {}
    if recovery.get("status") != "recovered":
        fail(f"recovery 异常: {recovery}")
    if not (inc.get("report") or {}).get("content"):
        fail("postmortem 报告缺失")
    print(f"[{time.time()-t0:5.1f}s] fix={fix_exec.get('status')} recovery=recovered 报告已生成")

    # 9) 清理
    requests.post(f"{ai}/api/demo/scenarios/SCN-001/reset", headers=HEADERS, timeout=15)
    print(f"[{time.time()-t0:5.1f}s] 环境已重置")
    print(f"\nPASS: Docker Compose 部署闭环完成,总耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
