"""M3 端到端验收:注入 → 自动调查 → awaiting_approval → 批准 → 执行 → recovered → postmortem。

前置:inventory-service(DEMO_MODE)、order-service、AI 服务(DEMO_MODE)已启动。
用法: python scripts/verify-m3.py
"""
import os
import subprocess
import sys
import time

import requests

AI = "http://localhost:8000"
DEMO_KEY = "demo-secret-2026"
HEADERS = {"x-demo-key": DEMO_KEY}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_load(seconds: int = 8, qps: int = 15) -> None:
    """制造故障态负载:让指标与 performance_schema digest 产生数据。"""
    env = {**os.environ, "LOAD_DURATION_SECONDS": str(seconds), "LOAD_QPS": str(qps)}
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "loadgen.py")],
                   env=env, cwd=ROOT, check=True, timeout=60)


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def wait_status(incident_id: str, target: str, timeout: int = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        inc = requests.get(f"{AI}/api/incidents/{incident_id}").json()
        if inc["status"] == target:
            return inc
        time.sleep(2)
    fail(f"incident {incident_id} 未在 {timeout}s 内到达 {target},当前 {inc.get('status')}")


def main() -> None:
    t0 = time.time()

    # 1) 重置 + 注入故障
    r = requests.post(f"{AI}/api/demo/scenarios/SCN-001/reset", headers=HEADERS, timeout=10)
    if r.status_code >= 400:
        fail(f"reset 失败: {r.status_code} {r.text[:200]}")
    print(f"[{time.time()-t0:5.1f}s] reset 完成")
    r = requests.post(f"{AI}/api/demo/scenarios/SCN-001/inject", headers=HEADERS, timeout=10)
    if r.status_code >= 400:
        fail(f"inject 失败: {r.status_code} {r.text[:200]}")
    print(f"[{time.time()-t0:5.1f}s] 故障已注入")

    # 2) 创建 Incident 并启动调查
    r = requests.post(f"{AI}/api/incidents", json={
        "title": "M3 验收:库存查询变慢",
        "description": "P95 异常,怀疑缺联合索引",
        "severity": "high",
        "service_ref": "inventory-service",
    }, timeout=10)
    if r.status_code != 201:
        fail(f"创建 incident 失败: {r.status_code} {r.text[:200]}")
    incident_id = str(r.json()["id"])
    print(f"[{time.time()-t0:5.1f}s] incident {incident_id} 已创建")

    # 2.5) 制造故障态负载(指标与 digest 增量数据),再启动调查
    run_load(seconds=8, qps=15)
    print(f"[{time.time()-t0:5.1f}s] 故障态负载完成")

    r = requests.post(f"{AI}/api/incidents/{incident_id}/investigations", timeout=10)
    if r.status_code != 202:
        fail(f"启动调查失败: {r.status_code} {r.text[:200]}")
    run_id = r.json()["run_id"]
    print(f"[{time.time()-t0:5.1f}s] 调查已启动 run={run_id}")

    # 3) 轮询到 awaiting_approval
    inc = wait_status(incident_id, "awaiting_approval")
    print(f"[{time.time()-t0:5.1f}s] 到达 awaiting_approval")

    # 4) 断言:假设 / E1~E5 证据 / approval pending
    hyps = inc.get("hypotheses") or []
    if not any("缺少联合索引" in (h.get("description") or "") for h in hyps):
        fail(f"假设缺失: {hyps}")
    evidence = inc.get("evidence") or []
    passed_keys = {e["key"] for e in evidence if e.get("passed")}
    for k in ("E1", "E2", "E3", "E4", "E5"):
        if k not in passed_keys:
            fail(f"证据 {k} 未通过: passed={sorted(passed_keys)} evidence={evidence}")
    print(f"[{time.time()-t0:5.1f}s] 假设与 E1~E5 证据齐备")

    approvals = inc.get("approvals") or []
    pending = [a for a in approvals if a.get("status") == "pending"]
    if not pending:
        fail(f"无 pending 审批: {approvals}")
    approval_id = pending[0]["id"]
    fix_proposal_id = pending[0]["fix_proposal_id"]
    print(f"[{time.time()-t0:5.1f}s] 审批 {approval_id} pending(提案 {fix_proposal_id})")

    # 5) 批准 → 轮询到 recovered
    r = requests.post(
        f"{AI}/api/incidents/{incident_id}/approvals/{approval_id}/decision",
        json={"decision": "approved", "comment": "M3 验收批准"}, timeout=15)
    if r.status_code >= 400:
        fail(f"审批失败: {r.status_code} {r.text[:200]}")
    inc = wait_status(incident_id, "recovered")
    print(f"[{time.time()-t0:5.1f}s] 已恢复(recovered)")

    # 6) 断言:fix_execution / recovery_check / postmortem
    fix_exec = inc.get("fix_execution") or {}
    if fix_exec.get("status") not in ("succeeded", "no_op"):
        fail(f"fix_execution 异常: {fix_exec}")
    recovery = inc.get("recovery") or {}
    if recovery.get("status") != "recovered":
        fail(f"recovery 异常: {recovery}")
    if not (inc.get("report") or {}).get("content"):
        fail("postmortem 报告缺失")
    print(f"[{time.time()-t0:5.1f}s] fix={fix_exec.get('status')} recovery={recovery.get('status')} 报告已生成")

    # 7) 清理
    requests.post(f"{AI}/api/demo/scenarios/SCN-001/reset", headers=HEADERS, timeout=10)
    print(f"[{time.time()-t0:5.1f}s] 环境已重置")
    print(f"\nPASS: 端到端闭环完成,总耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
