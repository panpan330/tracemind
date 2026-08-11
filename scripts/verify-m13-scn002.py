"""SCN-002 E2E:注入锁故障 → 健康/故障负载 → 创建 Incident → 调查(锁证据)→ 审批 → KILL → 恢复 → 报告。
每轮 finally reset,避免后台锁事务污染下一轮(设计 V1.3 §9 E2E 清理)。"""
import os
import subprocess
import sys
import time

import requests

AI = os.environ.get("AI_BASE", "http://localhost:8000")
ORDER = os.environ.get("ORDER_BASE", "http://localhost:8081")
HEADERS = {"x-demo-key": "demo-secret-2026"}


def p(msg: str) -> None:
    print(f"[{time.time() - t0:5.1f}s] {msg}", flush=True)


def run_load(seconds: int, qps: int) -> None:
    env = {**os.environ, "ORDER_SERVICE_URL": ORDER,
           "LOAD_DURATION_SECONDS": str(seconds), "LOAD_QPS": str(qps)}
    subprocess.run([sys.executable, "scripts/loadgen.py"], env=env, timeout=60,
                   capture_output=True)


def lock_load(seconds: int, qps: int = 10) -> None:
    """锁触发负载:循环 UPDATE 目标库存记录(42/7),该行被长事务持锁时阻塞。"""
    import pymysql
    conn = pymysql.connect(host="127.0.0.1", port=3306, user="app_business",
                           password="app_business_pwd", database="tracemind_business")
    deadline = time.time() + seconds
    try:
        with conn.cursor() as cur:
            while time.time() < deadline:
                cur.execute("UPDATE inventory SET quantity = quantity - 1 "
                            "WHERE sku_id = 42 AND warehouse_id = 7")
                conn.commit()
                time.sleep(1.0 / qps)
    except pymysql.OperationalError as exc:
        if "lock wait" in str(exc).lower() or exc.args and exc.args[0] == 1205:
            p(f"锁等待超时(预期): {exc}")
        else:
            raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def wait_status(incident_id: int, targets: set[str], timeout: int = 150) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = requests.get(f"{AI}/api/incidents/{incident_id}", timeout=10).json()
        if d["status"] in targets:
            return d
        time.sleep(3)
    return d


def main() -> int:
    global t0
    t0 = time.time()
    try:
        p("reset(SCN-002)")
        requests.post(f"{AI}/api/demo/scenarios/SCN-002/reset", headers=HEADERS, timeout=15)
        p("健康负载(基线,锁注入前,接口 42/7)")
        env_health = {**os.environ, "ORDER_SERVICE_URL": ORDER,
                      "LOAD_DURATION_SECONDS": "6", "LOAD_QPS": "15",
                      "LOAD_SKU": "42", "LOAD_WAREHOUSE": "7"}
        subprocess.run([sys.executable, "scripts/loadgen.py"], env=env_health, timeout=40,
                       capture_output=True)
        r = requests.post(f"{AI}/api/incidents", json={
            "title": "SCN-002 E2E", "description": "库存预占接口超时,疑似锁等待",
            "severity": "high", "service_ref": "inventory-service"}, timeout=10)
        inc = r.json()["id"]
        p(f"incident {inc}(健康基线已采集)")
        p("注入锁故障(SCN-002)")
        r = requests.post(f"{AI}/api/demo/scenarios/SCN-002/inject", headers=HEADERS, timeout=15)
        assert r.status_code == 200, f"inject 失败: {r.text}"
        st = requests.get(f"{AI}/api/demo/scenarios/SCN-002/status", headers=HEADERS,
                          timeout=10).json()
        assert st.get("lockHeld") is True, f"锁未持有: {st}"
        p("锁已持有,启动持续故障负载(后台)")
        env_lock = {**os.environ, "ORDER_SERVICE_URL": ORDER,
                    "LOAD_DURATION_SECONDS": "30", "LOAD_QPS": "15",
                    "LOAD_SKU": "42", "LOAD_WAREHOUSE": "7"}
        fault_proc = subprocess.Popen([sys.executable, "scripts/loadgen.py"], env=env_lock,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        lock_keep = subprocess.Popen([sys.executable, "-c", """
import pymysql, time
conn = pymysql.connect(host='127.0.0.1', port=3306, user='app_business',
                       password='app_business_pwd', database='tracemind_business')
cur = conn.cursor()
end = time.time() + 30
while time.time() < end:
    try:
        cur.execute('UPDATE inventory SET quantity=quantity-1 WHERE sku_id=42 AND warehouse_id=7')
        conn.commit()
    except Exception:
        pass
    time.sleep(0.4)
conn.close()
"""], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(12)   # 等待一个阻塞周期(锁超时 10s),让故障样本进入观测窗口
        r = requests.post(f"{AI}/api/incidents/{inc}/investigations", timeout=10)
        p(f"调查 run={r.json()['run_id']}")
        d = wait_status(inc, {"awaiting_approval", "needs_human", "failed"})
        if d["status"] != "awaiting_approval":
            p(f"FAIL: 未到 awaiting_approval, 实际 {d['status']} reason={d.get('termination_reason')}")
            return 1
        approval = [a for a in d["approvals"] if a["status"] == "pending"][0]
        r = requests.post(f"{AI}/api/incidents/{inc}/approvals/{approval['id']}/decision",
                          json={"decision": "approved", "comment": "E2E"}, timeout=180)
        assert r.status_code == 200, f"审批失败: {r.text}"
        d = wait_status(inc, {"recovered", "needs_human", "failed"}, timeout=120)
        if d["status"] != "recovered":
            p(f"FAIL: 未恢复, 实际 {d['status']} reason={d.get('termination_reason')}")
            return 1
        tc = d.get("tool_calls") or []
        transports = {t["transport"] for t in tc}
        assert "legacy_direct" not in transports, "出现 direct 回退"
        # 根因由 fix_proposal 的 action_type 体现(TERMINATE_BLOCKING_SESSION = 锁根因)
        prop = d.get("fix_proposal") or {}
        assert prop.get("action_type") == "TERMINATE_BLOCKING_SESSION", (
            f"根因不符: {prop.get('action_type')}")
        assert (d.get("report") or {}).get("content"), "复盘报告缺失"
        p("PASS: SCN-002 完整闭环(诊断→审批→KILL→恢复→报告)")
        return 0
    finally:
        p("finally reset(SCN-002)")
        try:
            for proc in (fault_proc, lock_keep):
                try:
                    proc.terminate()
                except Exception:
                    pass
            requests.post(f"{AI}/api/demo/scenarios/SCN-002/reset", headers=HEADERS, timeout=15)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
