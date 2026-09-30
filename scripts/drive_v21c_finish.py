# -*- coding: utf-8 -*-
"""一次性驱动:为调查中的 Run 注入负载并推进到终态(scn001 收尾)。用后即删。"""
import subprocess
import sys
import time

import pymysql
import requests

REPO = r"D:\wendang\TraceMind"
AI = "http://127.0.0.1:8000"


def q(sql, args=None):
    conn = pymysql.connect(host="127.0.0.1", user="tracemind_control_app",
                           password="control_app_pwd", database="tracemind_control",
                           autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args or ())
            return cur.fetchall()
    finally:
        conn.close()


def p(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


run_id, inc_id = int(sys.argv[1]), int(sys.argv[2])
env = {**__import__("os").environ, "ORDER_SERVICE_URL": "http://127.0.0.1:8081",
       "LOAD_DURATION_SECONDS": "100000", "LOAD_QPS": "20"}
proc = subprocess.Popen([sys.executable, REPO + r"\scripts\loadgen.py"], env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
p(f"负载已启动(pid={proc.pid})——注入缺索引故障流量")
approved = set()
final = None
try:
    deadline = time.time() + 420
    while time.time() < deadline and final is None:
        row = q("SELECT status FROM agent_run WHERE id=%s", (run_id,))[0][0]
        appr = q("SELECT id, status FROM approval WHERE incident_id=%s "
                 "ORDER BY id DESC LIMIT 1", (inc_id,))
        if appr and appr[0][0] not in approved and appr[0][1] == "pending":
            approved.add(appr[0][0])
            p(f"审批 #{appr[0][0]} → 批准")
            try:
                r = requests.post(
                    f"{AI}/api/incidents/{inc_id}/approvals/{appr[0][0]}/decision",
                    json={"decision": "approved"},
                    headers={"x-demo-key": "demo-secret-2026"}, timeout=60)
                p(f"审批响应: {r.status_code}")
            except requests.exceptions.ReadTimeout:
                p("审批响应超时(API 同步等图),CAS 已生效")
        if row in ("recovered", "needs_human", "failed", "self_recovered"):
            final = row
        time.sleep(5)
    p(f"Run 终态:{final}")

    # 停负载 → resolved → episode 关闭
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
    p("负载已停止,等待 resolved + episode 关闭")
    deadline = time.time() + 240
    while time.time() < deadline:
        row = q("SELECT lifecycle_status, open_group_key IS NOT NULL, closed_at "
                "FROM incident WHERE id=%s", (inc_id,))[0]
        if row[0] == "CLOSED":
            p(f"✓ episode 已关闭(closed_at={row[2]})")
            break
        time.sleep(5)
    print("Incident:", q("SELECT status, alert_status, lifecycle_status, "
                         "open_group_key, closed_at, baseline_quality, "
                         "termination_reason FROM incident WHERE id=%s", (inc_id,)))
    print("Run:", q("SELECT status, active_run_key, lease_owner, finished_at "
                    "FROM agent_run WHERE id=%s", (run_id,)))
    print("回放:", [r[0] for r in q(
        "SELECT step_type FROM incident_replay_step WHERE incident_id=%s ORDER BY id",
        (inc_id,))])
    print("事件:", dict((r[0], r[1]) for r in q(
        "SELECT event_type, COUNT(*) FROM incident_event WHERE incident_id=%s "
        "GROUP BY event_type", (inc_id,))))
    print("恢复验证:", q("SELECT status, latency_p95_after FROM recovery_check "
                         "WHERE incident_id=%s", (inc_id,)))
finally:
    if proc.poll() is None:
        proc.terminate()
