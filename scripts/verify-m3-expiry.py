"""M3 完成标准验证:过期审批自动扫描。

流程:注入 → 创建 incident → 调查到 awaiting_approval → 手动把 approval 过期
→ 等待 scanner(30s 周期)→ 断言 approval=expired 且 incident=rejected。
前置:三服务已启动(与 verify-m3 相同)。
用法: python scripts/verify-m3-expiry.py
"""
import os
import subprocess
import sys
import time

import pymysql
import requests

AI = "http://localhost:8000"
DEMO_KEY = "demo-secret-2026"
HEADERS = {"x-demo-key": DEMO_KEY}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = dict(
    host="localhost", port=3306, user="tracemind_control_app",
    password="control_app_pwd", database="tracemind_control", charset="utf8mb4",
)


def main() -> int:
    t0 = time.time()
    c = requests.Session()
    c.headers.update(HEADERS)

    c.post(f"{AI}/api/demo/scenarios/SCN-001/reset", timeout=10)
    c.post(f"{AI}/api/demo/scenarios/SCN-001/inject", timeout=10)
    inc = c.post(f"{AI}/api/incidents", json={
        "title": "过期审批验证", "severity": "high",
        "service_ref": "inventory-service"}).json()
    iid = inc["id"]

    env = {**os.environ, "LOAD_DURATION_SECONDS": "8", "LOAD_QPS": "15"}
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "loadgen.py")],
                   env=env, cwd=ROOT, check=True, timeout=60)
    c.post(f"{AI}/api/incidents/{iid}/investigations", timeout=10)

    # 等 awaiting_approval
    deadline = time.time() + 60
    status = None
    while time.time() < deadline:
        status = c.get(f"{AI}/api/incidents/{iid}").json()["status"]
        if status == "awaiting_approval":
            break
        time.sleep(2)
    if status != "awaiting_approval":
        print(f"FAIL: 未到达 awaiting_approval,当前 {status}")
        return 1
    print(f"[{time.time()-t0:5.1f}s] awaiting_approval,将审批过期")

    # 把最新 pending 审批过期
    conn = pymysql.connect(**DB)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE approval SET expires_at = UTC_TIMESTAMP() - INTERVAL 1 MINUTE "
            "WHERE incident_id = %s AND status = 'pending'", (iid,))
        conn.commit()
    conn.close()

    # 等待 scanner(周期 30s)处理
    print(f"[{time.time()-t0:5.1f}s] 等待过期扫描(≤35s)...")
    time.sleep(35)

    detail = c.get(f"{AI}/api/incidents/{iid}").json()
    approvals = detail.get("approvals") or []
    expired = [a for a in approvals if a.get("status") == "expired"]
    report = detail.get("report") or {}
    ok = bool(expired) and detail["status"] == "rejected" and bool(report.get("content"))
    print(f"[{time.time()-t0:5.1f}s] incident.status={detail['status']} "
          f"approval={[a['status'] for a in approvals]} 报告={'有' if report else '无'}")
    print("RESULT:", "PASS" if ok else "FAIL")
    c.post(f"{AI}/api/demo/scenarios/SCN-001/reset", timeout=10)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
