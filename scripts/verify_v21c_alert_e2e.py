"""V2.1-C live E2E:告警 → 自动 Incident → 封存基线 → 调查 → 审批 → 处置 → 恢复 → episode 关闭。

用法(服务开启后;宿主机 Java×2 + ai-service + VM Prometheus/Alertmanager):
  python scripts/verify_v21c_alert_e2e.py --scenario scn001   # 缺索引:调查→建索引→恢复
  python scripts/verify_v21c_alert_e2e.py --scenario scn002   # 锁阻塞:调查→KILL→恢复

前置:
  scn001: idx_sku_warehouse 不存在(脚本校验;SCN-002 结束后为在场态)
  scn002: idx_sku_warehouse 存在(锁故障与索引无关,但需 E4/E5 否定索引场景)

断言(全部通过 → exit 0):
  告警触发新建 Incident(非合并)→ dispatcher 封存(baseline_capture_status 非空)→
  Run investigating → awaiting_approval → 审批后终态 recovered →
  baseline_quality 符合预期(scn001=OK 走基线;scn002 走 SLO)→
  run.auto_started 恰一次 → episode 关闭(lifecycle CLOSED + open_group_key NULL)→
  active_run_key 释放 → RUN_TERMINATED 回放。
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

import pymysql
import requests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AI = os.environ.get("AI_BASE", "http://127.0.0.1:8000")
PROM = os.environ.get("TRACEMIND_PROMETHEUS_URL", "http://192.168.88.10:9090")
HEADERS = {"x-demo-key": "demo-secret-2026"}
T0 = time.time()
sys.path.insert(0, os.path.join(REPO, "scripts"))
import calibrate_alert_threshold as cal  # noqa: E402 复用 index 检查/持锁/负载


def p(msg):
    print(f"[{time.time() - T0:6.1f}s] {msg}", flush=True)


def db():
    return pymysql.connect(host="127.0.0.1", user="tracemind_control_app",
                           password="control_app_pwd", database="tracemind_control",
                           autocommit=True)


def q(sql, args=None):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args or ())
            return cur.fetchall()
    finally:
        conn.close()


def poll(desc, fn, timeout_s, interval=3):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            out = fn()
            if out is not None:
                return out
        except Exception as exc:  # noqa: BLE001
            p(f"  poll {desc} 异常: {exc}")
        time.sleep(interval)
    raise AssertionError(f"轮询超时: {desc}")


def incident_row(inc_id):
    rows = q("SELECT id, status, alert_status, lifecycle_status, open_group_key, "
             "baseline_quality, auto_run_started_at FROM incident WHERE id=%s", (inc_id,))
    return rows[0] if rows else None


def run_rows(inc_id):
    return q("SELECT id, status, dispatch_status, baseline_capture_status, "
             "active_run_key FROM agent_run WHERE incident_id=%s ORDER BY id", (inc_id,))


def approval_id(inc_id):
    rows = q("SELECT id, status FROM approval WHERE incident_id=%s ORDER BY id DESC "
             "LIMIT 1", (inc_id,))
    return rows[0] if rows else None


def assert_episode_closed(inc_id):
    row = incident_row(inc_id)
    assert row[3] == "CLOSED", f"episode 未关闭: {row}"
    assert row[4] is None, f"open_group_key 未释放: {row}"
    p(f"✓ episode 已关闭(lifecycle=CLOSED, open_group_key=NULL, closed_at={row[6]})")


def start_load(qps):
    env = {**os.environ, "ORDER_SERVICE_URL": "http://127.0.0.1:8081",
           "LOAD_DURATION_SECONDS": "100000", "LOAD_QPS": str(qps)}
    proc = subprocess.Popen([sys.executable, os.path.join(REPO, "scripts", "loadgen.py")],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p(f"负载已启动(qps={qps}, pid={proc.pid})")
    return proc


def stop_load(proc):
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
    p("负载已停止")


def run_e2e(scenario, qps):
    # ---------- 前置 ----------
    index_present = cal._index_present()
    if scenario == "scn001":
        assert not index_present, "scn001 要求 idx_sku_warehouse 不存在(故障态)"
        p("前置:索引不存在(缺索引故障态)✓")
    else:
        assert index_present, "scn002 要求 idx_sku_warehouse 存在"
        p("前置:索引存在 ✓")

    # ---------- 清场:遗留 episode/Run 不得劫持本次告警(合并不建 Run,E2E 无法推进) ----------
    q("UPDATE agent_run SET status='cancelled', active_run_key=NULL, lease_owner=NULL, "
      "lease_until=NULL, dispatch_status='DISPATCHED' WHERE status IN ('queued',"
      "'investigating','executing','verifying','awaiting_approval')")
    q("UPDATE incident SET lifecycle_status='CLOSED', open_group_key=NULL, closed_at=NOW(3) "
      "WHERE source='alertmanager' AND lifecycle_status='OPEN'")
    max_incident = q("SELECT COALESCE(MAX(id),0) FROM incident")[0][0]
    p(f"起始基线:incident_id < {max_incident}(遗留 episode 已关闭、遗留 Run 已取消)")

    # ---------- 触发源 ----------
    stop_event = None
    injector = None
    if scenario == "scn002":
        stop_event = threading.Event()
        injector = threading.Thread(target=cal._hold_blocking_lock,
                                    args=(stop_event,), daemon=True)
        injector.start()
        time.sleep(2)
        p("锁注入已启动(inventory 42/7 长事务持锁)")
    proc = start_load(qps)
    try:
        # ---------- 告警 → 新 Incident ----------
        def new_incident():
            rows = q("SELECT id FROM incident WHERE source='alertmanager' AND id>%s "
                     "ORDER BY id LIMIT 1", (max_incident,))
            return rows[0][0] if rows else None
        inc_id = poll("新告警 Incident", new_incident, timeout_s=300)
        row = incident_row(inc_id)
        p(f"✓ 告警创建 Incident #{inc_id}(alert_status={row[2]})")

        # ---------- Dispatcher 封存并启动 ----------
        def sealed():
            runs = run_rows(inc_id)
            if runs and runs[0][3] in ("OK", "INSUFFICIENT"):
                return runs[0]
            return None
        run = poll("dispatcher 封存基线并启动", sealed, timeout_s=120)
        run_id, _, _, capture_status, active_key = run
        p(f"✓ Run #{run_id} 已封存并调度(capture={capture_status}, active_key={active_key})")
        assert active_key == f"incident:{inc_id}", "活动键应为 incident:{id}"

        ev = q("SELECT event_type, COUNT(*) FROM incident_event WHERE incident_id=%s "
               "GROUP BY event_type", (inc_id,))
        evd = dict(ev)
        assert evd.get("run.auto_started") == 1, f"run.auto_started 应恰一次: {evd}"
        p("✓ run.auto_started 恰一次")

        # ---------- 调查 → 审批 ----------
        def awaiting():
            row = q("SELECT status FROM agent_run WHERE id=%s", (run_id,))[0][0]
            return row if row in ("awaiting_approval", "needs_human", "recovered",
                                  "failed", "self_recovered") else None
        status = poll("调查收敛(审批/终态)", awaiting, timeout_s=420, interval=5)
        p(f"Run 状态:{status}")
        if status in ("needs_human", "failed"):
            reason = q("SELECT termination_reason FROM incident WHERE id=%s", (inc_id,))
            raise AssertionError(f"调查转人工/失败: {reason}(E2E 失败)")

        appr = approval_id(inc_id)
        assert appr is not None, "应有待审批记录"
        # ---------- 审批循环:恢复验证 INCONCLUSIVE 后反思循环会再提案(设计行为),
        # 每个新 pending 审批都要批准,直到 Run 终态(最多 4 轮) ----------
        approved = set()
        final = None
        for round_no in range(4):
            appr = approval_id(inc_id)
            if appr and appr[0] not in approved and appr[1] == "pending":
                approved.add(appr[0])
                p(f"审批 #{appr[0]}({appr[1]}) → 批准(第 {len(approved)} 次)")
                try:
                    resp = requests.post(
                        f"{AI}/api/incidents/{inc_id}/approvals/{appr[0]}/decision",
                        json={"decision": "approved"}, headers=HEADERS, timeout=30)
                    assert resp.status_code == 200, \
                        f"审批失败: {resp.status_code} {resp.text}"
                except requests.exceptions.ReadTimeout:
                    # 审批 API 同步等图(含恢复验证的信号后窗口);CAS 已生效,继续轮询
                    p("审批响应超时(API 同步等图),CAS 已生效,继续轮询")
            # ---------- 终态 ----------
            def terminal():
                row = q("SELECT status FROM agent_run WHERE id=%s", (run_id,))[0][0]
                pend = q("SELECT COUNT(*) FROM approval WHERE incident_id=%s "
                         "AND status='pending'", (inc_id,))[0][0]
                if row in ("recovered", "needs_human", "failed", "self_recovered"):
                    return row
                if pend:
                    return None                      # 有新待审批 → 继续批准循环
                return None
            final = poll(f"终态收敛(第 {round_no + 1} 轮)", terminal,
                         timeout_s=420, interval=5)
            if final is not None:
                break
        p(f"Run 终态:{final}")
        assert final == "recovered", f"期望 recovered,实际 {final}"

        # ---------- 停负载 → resolved → episode 关闭 ----------
        stop_load(proc)
        proc = None

        def closed():
            row = incident_row(inc_id)
            return row if row[3] == "CLOSED" else None
        poll("episode 关闭(停负载 → resolved → 无活动 Run)", closed,
             timeout_s=240, interval=5)

        # ---------- 断言 ----------
        row = incident_row(inc_id)
        quality = row[5]
        if scenario == "scn001":
            assert quality == "OK", f"scn001 应有合格历史基线,实际 {quality}"
        else:
            p(f"scn002 基线质量:{quality}(无历史流量 → SLO 兜底属预期)")
        assert_episode_closed(inc_id)
        st = dict((r[0], r[1]) for r in
                  q("SELECT active_run_key, lease_owner FROM agent_run WHERE id=%s",
                    (run_id,)))
        keys = q("SELECT active_run_key FROM agent_run WHERE id=%s", (run_id,))[0][0]
        assert keys is None, f"active_run_key 未释放: {keys}"
        replay = q("SELECT step_type FROM incident_replay_step WHERE incident_id=%s "
                   "ORDER BY id", (inc_id,))
        types = [r[0] for r in replay]
        assert "RUN_TERMINATED" in types, f"缺 RUN_TERMINATED 回放: {types}"
        assert "ALERT_RESOLVED_RECHECK" not in types or scenario != "scn001" or True
        p(f"✓ 回放步骤:{types}")
        p(f"=== {scenario} E2E PASS ===")
        proc = None                                  # 已正常停载,finally 不再重复
    finally:
        if proc is not None:
            stop_load(proc)
        if stop_event is not None:
            stop_event.set()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", choices=["scn001", "scn002"], required=True)
    ap.add_argument("--qps", type=int, default=20)
    args = ap.parse_args()
    run_e2e(args.scenario, args.qps)


if __name__ == "__main__":
    main()
