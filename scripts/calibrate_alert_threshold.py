"""ORDER_CREATE HTTP P95 阈值校准(V2.1-C 方案 §V2.1 完成标准前置门,计划 v2 §六)。

目的:证明"健康分布低于阈值、故障分布越过阈值",并以实测分布回填
TRACEMIND_BASELINE_MAX_P95_MS / TRACEMIND_SLO_P95_MS / TRACEMIND_BASELINE_MIN_SAMPLES。
live 验收(--tier vm-smoke 的告警链路部分)前必须先出校准报告。

用法(服务开启后:VM Docker Prometheus 可达 + 本机 order/inventory 可达):
  # 1) 分阶段采集(每阶段:预检 → loadgen 负载 → Prometheus 区间采样 → 分布统计)
  python scripts/calibrate_alert_threshold.py --phase healthy --duration 300 --qps 20
  python scripts/calibrate_alert_threshold.py --phase scn001 --duration 300 --qps 20
  python scripts/calibrate_alert_threshold.py --phase scn002 --duration 180 --qps 10 --inject
  # 2) 断言 + 建议配置(健康上界 < 阈值 < 故障下界)
  python scripts/calibrate_alert_threshold.py --verify --threshold 100

阶段前置(不满足即拒绝,不做任何 DDL):
  healthy: idx_sku_warehouse 必须存在(无故障态)
  scn001 : idx_sku_warehouse 必须不存在(缺索引故障,demo 默认态;由操作者保证)
  scn002 : --inject 时脚本自带长事务持锁(与 verify-m13 同法,finally 回滚);
           无 --inject 时要求操作者自行注入阻塞事务

报告:reports/calibration/alert-threshold-calibration-<ts>.md / .json
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

import requests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORDER_URL = os.environ.get("ORDER_SERVICE_URL", "http://localhost:8081")
PROM_URL = os.environ.get("TRACEMIND_PROMETHEUS_URL", "http://localhost:9090")
P95_TEMPLATE = ('histogram_quantile(0.95, sum by (le) ('
                'rate(http_server_requests_seconds_bucket{service="order-service",'
                'uri="/api/orders/{orderId}/check-stock"}[%(window)s])))')
URI_TEMPLATE = "/api/orders/{orderId}/check-stock"
PHASES = ("healthy", "scn001", "scn002")
REPORT_DIR = os.path.join(REPO, "reports", "calibration")


# ---------- 纯函数(单测覆盖) ----------

def percentile(sorted_values, pct):
    """最近邻百分位(输入须升序);空序列 → None。"""
    if not sorted_values:
        return None
    idx = min(len(sorted_values) - 1, max(0, int(round(pct / 100.0 * (len(sorted_values) - 1)))))
    return sorted_values[idx]


def summarize(samples_ms):
    """分布摘要(ms):count/min/p50/p90/max;空 → count=0 其余 None。"""
    vals = sorted(float(v) for v in samples_ms)
    return {"count": len(vals), "min": percentile(vals, 0),
            "p50": percentile(vals, 50), "p90": percentile(vals, 90),
            "max": percentile(vals, 100)}


def parse_p95_series(body, min_samples):
    """解析 Prometheus query_range 响应 → P95 毫秒样本列表。

    空结果/非 success → ValueError(采样失败,不伪造分布)。"""
    if body.get("status") != "success":
        raise ValueError("prometheus query_range failed")
    result = (body.get("data") or {}).get("result") or []
    if not result:
        raise ValueError("no series for ORDER_CREATE p95")
    values = result[0].get("values") or []
    samples = []
    for _, raw in values:
        try:
            samples.append(round(float(raw) * 1000.0, 1))
        except (TypeError, ValueError):
            continue
    if len(samples) < min_samples:
        raise ValueError(f"insufficient p95 samples: {len(samples)} < {min_samples}")
    return samples


def summarize_phase(samples_ms, min_samples):
    """阶段分布 + 样本充分性(不足 → insufficient,校准断言按失败处理)。"""
    summary = summarize(samples_ms)
    summary["insufficient"] = summary["count"] < min_samples
    return summary


def recommend_threshold(healthy_max_ms, fault_min_ms):
    """建议阈值:健康上界 ×1.5 向上取整到 10ms,且必须低于故障下界;否则 None。"""
    if healthy_max_ms is None or fault_min_ms is None or fault_min_ms <= healthy_max_ms:
        return None
    raw = healthy_max_ms * 1.5
    return min(int((raw + 9) // 10 * 10), int(fault_min_ms) - 1)


def evaluate_calibration(phases, threshold_ms, min_samples):
    """校准断言:健康分布上界 < 阈值 < 各故障分布下界;样本不足按失败。

    返回 {verdict, healthy_ok, faults:{phase:{min,ok,insufficient}}, recommended_ms}。"""
    healthy = phases.get("healthy") or {}
    healthy_ok = bool(healthy) and not healthy.get("insufficient") \
        and healthy["max"] is not None and healthy["max"] < threshold_ms
    faults = {}
    for phase, summary in (phases or {}).items():
        if phase == "healthy":
            continue
        insufficient = bool(summary.get("insufficient"))
        fmin = summary.get("min")
        faults[phase] = {"min": fmin, "insufficient": insufficient,
                         "ok": (not insufficient) and fmin is not None
                         and fmin > threshold_ms}
    fault_mins = [f["min"] for f in faults.values() if f["min"] is not None]
    recommended = recommend_threshold(
        healthy.get("max"), min(fault_mins) if fault_mins else None)
    verdict = "PASS" if healthy_ok and faults and all(f["ok"] for f in faults.values()) \
        else "FAIL"
    return {"verdict": verdict, "healthy_ok": healthy_ok, "faults": faults,
            "threshold_ms": threshold_ms, "recommended_ms": recommended,
            "min_samples": min_samples}


def render_markdown(report):
    """校准报告(markdown):各阶段分布 + 断言结论 + 建议配置。"""
    lines = ["# ORDER_CREATE HTTP P95 阈值校准报告", "",
             f"- 生成时间:{report.get('generated_at')}",
             f"- Prometheus:{report.get('prometheus')}",
             f"- 阈值:{report.get('threshold_ms')} ms;"
             f"最小样本数:{report.get('min_samples')}", ""]
    for phase, summary in (report.get("phases") or {}).items():
        lines.append(f"## 阶段:{phase}")
        if not summary:
            lines.append("- 未采集")
            continue
        lines.append(f"- 样本数:{summary['count']}(不足下限={summary.get('insufficient')})")
        for key, label in (("min", "下界"), ("p50", "P50"), ("p90", "P90"),
                           ("max", "上界")):
            v = summary.get(key)
            lines.append(f"- {label}:{'—' if v is None else f'{v} ms'}")
        lines.append("")
    ev = report.get("evaluation") or {}
    lines += ["## 断言", f"- 结论:**{ev.get('verdict')}**(阈值 {ev.get('threshold_ms')} ms)",
              f"- 健康上界 < 阈值:{'PASS' if ev.get('healthy_ok') else 'FAIL'}"]
    for phase, f in (ev.get("faults") or {}).items():
        lines.append(f"- {phase} 下界 > 阈值:"
                     f"{'PASS' if f['ok'] else 'FAIL'}(下界 {f['min']} ms)")
    rec = ev.get("recommended_ms")
    lines += ["", "## 建议配置(回填 ai-service/.env.local 与 compose)",
              "```",
              f"TRACEMIND_BASELINE_MAX_P95_MS={rec if rec else '<人工复核>'}",
              f"TRACEMIND_SLO_P95_MS={rec if rec else '<人工复核>'}",
              f"TRACEMIND_BASELINE_MIN_SAMPLES={ev.get('min_samples')}",
              "```", ""]
    return "\n".join(lines)


# ---------- 采集与注入(live 阶段使用;不做任何 DDL) ----------

def _db_conn():
    import pymysql
    return pymysql.connect(
        host=os.environ.get("TRACEMIND_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("TRACEMIND_DB_PORT", "3306")),
        user=os.environ.get("TRACEMIND_DB_APP_BUSINESS_USER", "app_business"),
        password=os.environ.get("TRACEMIND_DB_APP_BUSINESS_PASSWORD",
                                "app_business_pwd"),
        database="tracemind_business")


def _index_present():
    with _db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = 'inventory' "
                "AND index_name = 'idx_sku_warehouse'")
            return cur.fetchone()[0] > 0


def _check_phase_precondition(phase):
    """阶段前置检查(healthy=有索引,scn001=无索引,scn002=无要求;不做 DDL)。"""
    if phase == "healthy" and not _index_present():
        sys.exit("healthy 阶段要求 idx_sku_warehouse 存在(先修复或手动建索引)")
    if phase == "scn001" and _index_present():
        sys.exit("scn001 阶段要求 idx_sku_warehouse 不存在(演示默认态;脚本不做 DDL)")


def _hold_blocking_lock(stop_event):
    """scn002 注入:长事务持有 inventory(42,7) 行锁(与 verify-m13 同法)。"""
    import pymysql
    conn = pymysql.connect(
        host=os.environ.get("TRACEMIND_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("TRACEMIND_DB_PORT", "3306")),
        user=os.environ.get("TRACEMIND_DB_APP_BUSINESS_USER", "app_business"),
        password=os.environ.get("TRACEMIND_DB_APP_BUSINESS_PASSWORD",
                                "app_business_pwd"),
        database="tracemind_business", autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute("START TRANSACTION")
            cur.execute("UPDATE inventory SET quantity = quantity "
                        "WHERE sku_id = 42 AND warehouse_id = 7")
            while not stop_event.wait(1.0):
                cur.execute("SELECT 1")   # 保活,事务保持开启
        conn.rollback()
    finally:
        conn.close()


def _run_load(duration_s, qps):
    env = {**os.environ, "ORDER_SERVICE_URL": ORDER_URL,
           "LOAD_DURATION_SECONDS": str(duration_s), "LOAD_QPS": str(qps)}
    subprocess.run([sys.executable, os.path.join(REPO, "scripts", "loadgen.py")],
                   env=env, timeout=duration_s + 60, check=False)


def _sample_p95_window(start_ts, end_ts, window_s, step_s, min_samples):
    """Prometheus query_range 采样窗口内 ORDER_CREATE P95(固定模板,精确 uri)。"""
    body = requests.get(f"{PROM_URL}/api/v1/query_range", params={
        "query": P95_TEMPLATE % {"window": f"{window_s}s"},
        "start": start_ts, "end": end_ts, "step": f"{step_s}s",
    }, timeout=30).json()
    return parse_p95_series(body, min_samples)


def collect_phase(phase, duration_s, qps, window_s, step_s, min_samples, inject):
    """单阶段采集:预检 → (可选注入) → 负载 → 采样 → 分布。"""
    _check_phase_precondition(phase)
    stop_event = None
    injector = None
    if phase == "scn002" and inject:
        stop_event = threading.Event()
        injector = threading.Thread(target=_hold_blocking_lock, args=(stop_event,),
                                    daemon=True)
        injector.start()
        time.sleep(2)                     # 等锁事务就绪
    start_ts = time.time()
    try:
        _run_load(duration_s, qps)
    finally:
        end_ts = time.time()
        if stop_event is not None:
            stop_event.set()
        if injector is not None:
            injector.join(10)
    samples = _sample_p95_window(start_ts - window_s, end_ts + window_s,
                                 window_s, step_s, min_samples)
    return summarize_phase(samples, min_samples)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--phase", choices=PHASES, help="采集阶段")
    ap.add_argument("--verify", action="store_true", help="汇总断言已有采集结果")
    ap.add_argument("--threshold", type=int, default=100, help="断言阈值 ms(默认 100)")
    ap.add_argument("--duration", type=int, default=300, help="负载时长 s")
    ap.add_argument("--qps", type=int, default=20, help="负载 QPS")
    ap.add_argument("--window", type=int, default=60, help="P95 rate 窗口 s")
    ap.add_argument("--step", type=int, default=15, help="采样步长 s")
    ap.add_argument("--min-samples", type=int, default=30, help="阶段最少 P95 样本数")
    ap.add_argument("--inject", action="store_true",
                    help="scn002 由脚本注入阻塞长事务")
    args = ap.parse_args()

    state_path = os.path.join(REPORT_DIR, "phases-latest.json")
    if args.verify or not args.phase:
        if not os.path.exists(state_path):
            sys.exit("未找到采集结果:先用 --phase 分阶段采集")
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    else:
        print(f"[{args.phase}] 预检 + 采集({args.duration}s @ {args.qps}qps)...")
        summary = collect_phase(args.phase, args.duration, args.qps, args.window,
                                args.step, args.min_samples, args.inject)
        print(f"[{args.phase}] {summary}")
        state = {}
        if os.path.exists(state_path):
            with open(state_path, encoding="utf-8") as f:
                state = json.load(f)
        state[args.phase] = summary
        os.makedirs(REPORT_DIR, exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    evaluation = evaluate_calibration(state, args.threshold, args.min_samples)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "prometheus": PROM_URL, "phases": state,
              "threshold_ms": args.threshold, "min_samples": args.min_samples,
              "evaluation": evaluation}
    os.makedirs(REPORT_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    md_path = os.path.join(REPORT_DIR, f"alert-threshold-calibration-{ts}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(report))
    with open(md_path.replace(".md", ".json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(render_markdown(report))
    print(f"报告:{md_path}")
    if args.verify or (len(state) >= len(PHASES)):
        sys.exit(0 if evaluation["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
