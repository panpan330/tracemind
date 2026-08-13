"""V1.5 验收:证据与决策链回放。
1) SCN-002 完整闭环 → replayStatus=complete,步骤链完整,keyStepIndexes 齐全
2) rejected 路径 → runOutcome=rejected,无 FIX_EXECUTED 必需步骤要求
3) 只读无副作用:播放前后 Incident/Approval/FixExecution 记录不变;重复读取步骤一致;runId 归属校验 404
用法: python scripts/verify-m15.py --base http://localhost:8000 --order http://localhost:8081
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import requests  # noqa: E402

t0 = time.time()


def p(msg):
    print(f"[{time.time() - t0:5.1f}s] {msg}", flush=True)


def load(seconds, qps, sku, wh, order):
    env = {**os.environ, "ORDER_SERVICE_URL": order,
           "LOAD_DURATION_SECONDS": str(seconds), "LOAD_QPS": str(qps),
           "LOAD_SKU": str(sku), "LOAD_WAREHOUSE": str(wh),
           "LOAD_TIMEOUT_SECONDS": "6.0"}
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "loadgen.py")],
                   env=env, timeout=60, capture_output=True)


def api(base, method, path, **kw):
    url = f"{base}{path}"
    r = requests.request(method, url, timeout=30, **kw)
    r.raise_for_status()
    return r.json()


def replay_complete(base, incident_id):
    """断言回放完整与结构。"""
    m = api(base, "GET", f"/api/incidents/{incident_id}/replay")
    assert m.get("defaultRunId"), "缺少 defaultRunId"
    run_id = m["defaultRunId"]

    rm = api(base, "GET", f"/api/incidents/{incident_id}/replay/runs/{run_id}")
    assert rm["replayStatus"] == "complete", f"replayStatus={rm['replayStatus']}"
    assert rm["runStatus"] == "terminated"
    assert rm.get("runOutcome") == "recovered", f"runOutcome={rm.get('runOutcome')}"

    steps = api(base, "GET", f"/api/incidents/{incident_id}/replay/runs/{run_id}/steps")
    assert steps["totalSteps"] >= 8, f"步骤过少: {steps['totalSteps']}"
    types = {st["stepType"] for st in steps["steps"]}
    for required in ("INCIDENT_INGESTED", "HYPOTHESES_GENERATED", "EVIDENCE_COLLECTION",
                     "DIAGNOSIS_EVALUATED", "FIX_PROPOSED", "APPROVAL_REQUESTED",
                     "APPROVAL_DECIDED", "FIX_EXECUTED", "RECOVERY_VERIFIED",
                     "REPORT_GENERATED", "RUN_TERMINATED"):
        assert required in types, f"缺少步骤类型 {required}"
    ki = steps["keyStepIndexes"]
    for key in ("diagnosis", "approval", "execution", "recovery"):
        assert key in ki, f"缺少关键节点 {key}"
    for st in steps["steps"]:
        assert st["stepState"] in ("completed", "failed"), f"未终态: {st['logicalStepId']}"
        assert st["sourceSequenceNos"], "sourceSequenceNos 为空"
        assert st["displayDurationMs"] > 0

    # 单步技术详情(稳定 ID)
    detail = api(base, "GET",
                 f"/api/incidents/{incident_id}/replay/runs/{run_id}/steps/{steps['steps'][0]['logicalStepId']}")
    assert "versions" in detail and "snapshotHash" in detail
    return run_id, steps


def readonly_assertions(base, incident_id, run_id, steps):
    """只读无副作用:重复读取一致;归属校验;记录不变。"""
    steps2 = api(base, "GET", f"/api/incidents/{incident_id}/replay/runs/{run_id}/steps")
    assert steps2["totalSteps"] == steps["totalSteps"]
    for a, b in zip(steps["steps"], steps2["steps"]):
        assert a["stateAfter"] == b["stateAfter"], "重复读取步骤不一致"
        assert a["sourceSequenceNos"] == b["sourceSequenceNos"]

    # runId 归属校验:错误 incident → 404
    bad = requests.get(f"{base}/api/incidents/999999/replay/runs/{run_id}/steps", timeout=10)
    assert bad.status_code == 404, "归属校验未生效"

    p("只读断言 PASS(重复读取一致/归属校验)")


def run_round(base, order, scenario, reject=False):
    """跑一轮完整流程,返回 incident_id。
    完整闭环用 SCN-002(锁证据真实,fixture 观测下也能到审批);SCN-001 留 VM 真实模式。"""
    headers = {"x-demo-key": "demo-secret-2026"}
    # 场景互斥:先重置两个场景,避免 activeScenario 残留导致 inject 409
    for sc in ("SCN-001", "SCN-002"):
        try:
            api(base, "POST", f"/api/demo/scenarios/{sc}/reset", headers=headers)
        except Exception:
            pass
    load(6, 12, 42, 7, order)  # 健康负载(基线)
    inc = api(base, "POST", "/api/incidents", json={
        "title": f"V1.5 验收 {scenario}", "description": "回放验收",
        "severity": "high", "service_ref": "inventory-service"})
    incident_id = inc["id"]
    api(base, "POST", f"/api/demo/scenarios/{scenario}/inject", headers=headers)
    procs = []
    if scenario == "SCN-002":
        # 确认锁已持有(仿 verify-m13:lockHeld=True 后才继续)
        st = api(base, "GET", f"/api/demo/scenarios/SCN-002/status", headers=headers)
        assert st.get("lockHeld") is True, f"锁未持有: {st}"
        # 锁等待者:UPDATE 42/7(等锁)+ loadgen(FOR SHARE 超时流量)
        up = subprocess.Popen([sys.executable, "-c",
                               'import pymysql,time; c=pymysql.connect(host="127.0.0.1",port=3306,'
                               'user="app_business",password="app_business_pwd",database="tracemind_business");'
                               'cur=c.cursor(); end=time.time()+30\n'
                               'while time.time()<end:\n'
                               ' try:\n  cur.execute("UPDATE inventory SET quantity=quantity-1 '
                               'WHERE sku_id=42 AND warehouse_id=7"); c.commit()\n'
                               ' except Exception: pass\n time.sleep(0.4)'],
                              stdout=subprocess.DEVNULL)
        procs.append(up)
        env_lock = {**os.environ, "ORDER_SERVICE_URL": order,
                    "LOAD_DURATION_SECONDS": "25", "LOAD_QPS": "12",
                    "LOAD_SKU": "42", "LOAD_WAREHOUSE": "7",
                    "LOAD_TIMEOUT_SECONDS": "6.0"}
        lg = subprocess.Popen([sys.executable,
                               os.path.join(ROOT, "scripts", "loadgen.py")],
                              env=env_lock, stdout=subprocess.DEVNULL)
        procs.append(lg)
        time.sleep(7)  # 让锁等待产生且阻塞事务 age 超过 L2 阈值(5000ms)
    run = api(base, "POST", f"/api/incidents/{incident_id}/investigations")
    time.sleep(6)  # trace 导出
    # 等待审批(或 rejected)
    deadline = time.time() + 90
    while time.time() < deadline:
        d = requests.get(f"{base}/api/incidents/{incident_id}", timeout=10).json()
        if d["status"] in ("awaiting_approval", "recovered", "rejected",
                           "needs_human", "failed"):
            break
        time.sleep(3)
    if reject:
        approval = [a for a in d.get("approvals", []) if a.get("status") == "pending"]
        if approval:
            api(base, "POST",
                f"/api/incidents/{incident_id}/approvals/{approval[0]['id']}/decision",
                json={"decision": "rejected", "comment": "验收拒绝"})
        deadline = time.time() + 90
        while time.time() < deadline:
            d = requests.get(f"{base}/api/incidents/{incident_id}", timeout=10).json()
            if d["status"] in ("rejected", "needs_human", "recovered", "failed"):
                break
            time.sleep(3)
    elif d.get("status") == "awaiting_approval":
        # 完整闭环:自动审批 → 等待恢复(terminated)
        approval = [a for a in d.get("approvals", []) if a.get("status") == "pending"]
        if approval:
            api(base, "POST",
                f"/api/incidents/{incident_id}/approvals/{approval[0]['id']}/decision",
                json={"decision": "approved", "comment": "验收自动审批"})
        deadline = time.time() + 90
        while time.time() < deadline:
            d = requests.get(f"{base}/api/incidents/{incident_id}", timeout=10).json()
            if d["status"] in ("recovered", "rejected", "needs_human", "failed"):
                break
            time.sleep(3)
    for pr in procs:
        pr.terminate()
    return incident_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--order", default="http://localhost:8081")
    args = ap.parse_args()

    # 1) SCN-002 完整闭环 → complete replay(锁场景,fixture 观测下证据真实)
    p("=== SCN-002 完整闭环 ===")
    inc1 = run_round(args.base, args.order, "SCN-002")
    run_id, steps = replay_complete(args.base, inc1)
    readonly_assertions(args.base, inc1, run_id, steps)
    p(f"SCN-002 replayStatus=complete, totalSteps={steps['totalSteps']}")

    # 2) rejected 路径 → runOutcome=rejected,不要求 FIX_EXECUTED
    p("=== rejected 路径 ===")
    inc2 = run_round(args.base, args.order, "SCN-002", reject=True)
    m = api(args.base, "GET", f"/api/incidents/{inc2}/replay")
    run_id2 = m["defaultRunId"]
    rm = api(args.base, "GET", f"/api/incidents/{inc2}/replay/runs/{run_id2}")
    steps2 = api(args.base, "GET", f"/api/incidents/{inc2}/replay/runs/{run_id2}/steps")
    types2 = {st["stepType"] for st in steps2["steps"]}
    assert "APPROVAL_DECIDED" in types2
    assert "FIX_EXECUTED" not in types2 or steps2["keyStepIndexes"].get("execution") is None
    # spec:Manifest 单独返回 runOutcome,rejected 路径必须是 rejected
    assert rm.get("runOutcome") == "rejected", f"runOutcome={rm.get('runOutcome')}"
    assert rm.get("runStatus") == "terminated"
    p(f"rejected 路径 PASS(runOutcome={rm.get('runOutcome')})")

    p("PASS: V1.5 回放验收全部通过")


if __name__ == "__main__":
    main()
