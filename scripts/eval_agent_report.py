"""真实模型量化评测:跑 SCN-001/002 各 N 轮(对齐 verify-m14 故障注入+负载时序),拉观测数据汇总报告。"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
HEADERS = {"x-demo-key": "demo-secret-2026"}
_order_url = "http://localhost:8081"
_order_host = "localhost"


def load(seconds: int, qps: int, sku: int | None = None, wh: int | None = None,
         max_in_flight: int = 1, timeout: float = 8.0) -> None:
    env = {**os.environ, "ORDER_SERVICE_URL": _order_url,
           "LOAD_DURATION_SECONDS": str(seconds), "LOAD_QPS": str(qps),
           "LOAD_MAX_IN_FLIGHT": str(max_in_flight), "LOAD_TIMEOUT_SECONDS": str(timeout)}
    if sku is not None:
        env["LOAD_SKU"] = str(sku)
        env["LOAD_WAREHOUSE"] = str(wh or 0)
    subprocess.run([sys.executable, str(ROOT / "scripts/loadgen.py")],
                   env=env, timeout=seconds + 30, capture_output=True)


def _api(base: str, path: str, method="get", **kw):
    fn = getattr(requests, method)
    r = fn(f"{base}{path}", timeout=30, **kw)
    r.raise_for_status()
    return r.json()


def _wait_status(base, incident_id, targets, timeout_s=120):
    t0 = time.time()
    status = "unknown"
    while time.time() - t0 < timeout_s:
        d = _api(base, f"/api/incidents/{incident_id}")
        status = d["status"]
        if status in targets:
            return status
        time.sleep(2)
    return status


def _lock_load():
    code = ('import pymysql,time; c=pymysql.connect(host="%s",port=3306,'
            'user="app_business",password="app_business_pwd",database="tracemind_business");'
            'cur=c.cursor(); end=time.time()+30\n'
            'while time.time()<end:\n'
            ' try:\n  cur.execute("UPDATE inventory SET quantity=quantity-1 '
            'WHERE sku_id=42 AND warehouse_id=7"); c.commit()\n'
            ' except: pass\n time.sleep(0.4)') % _order_host
    return subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.DEVNULL)


def run_one_round(base: str, scenario: str, round_no: int) -> dict:
    # 场景互斥:inject SCN-002 需 SCN-001 已恢复(反之亦然),故每轮前清两个场景
    for s in ("SCN-001", "SCN-002"):
        _api(base, f"/api/demo/scenarios/{s}/reset", method="post", headers=HEADERS)
    # 健康负载(基线:保证 E3 digest 增量可测)
    load(6, 12)
    # 创建 incident(带 description + 受影响操作,供 LLM 生成假设)
    inc = _api(base, "/api/incidents", method="post",
               json={"title": f"{scenario} eval", "description": f"真实模型评测 {scenario}",
                     "severity": "high", "service_ref": "inventory-service",
                     "affected_service_ref": "inventory-service",
                     "affected_operation_ref": ("INVENTORY_LOOKUP" if scenario == "SCN-001"
                                                else "INVENTORY_RESERVATION")})
    incident_id = inc["id"]
    # 注入故障
    _api(base, f"/api/demo/scenarios/{scenario}/inject", method="post", headers=HEADERS)
    # 先启动调查(采集 digest 基线),再打故障负载
    run = _api(base, f"/api/incidents/{incident_id}/investigations", method="post")
    run_id = run["run_id"]
    t0 = time.time()
    procs = []
    if scenario == "SCN-002":
        procs.append(_lock_load())
        env = {**os.environ, "ORDER_SERVICE_URL": _order_url,
               "LOAD_DURATION_SECONDS": "25", "LOAD_QPS": "15",
               "LOAD_MAX_IN_FLIGHT": "1", "LOAD_TIMEOUT_SECONDS": "6.0",
               "LOAD_SKU": "42", "LOAD_WAREHOUSE": "7"}
        procs.append(subprocess.Popen([sys.executable, str(ROOT / "scripts/loadgen.py")],
                                      env=env, stdout=subprocess.DEVNULL))
        time.sleep(3)  # 让锁等待/超时流量产生
    else:
        load(8, 15, sku=42, wh=7, max_in_flight=1, timeout=6.0)
    time.sleep(14)  # 等 OTel batch(5s)+ Jaeger 完成 trace 导出
    status = _wait_status(base, incident_id, {"awaiting_approval", "needs_human", "recovered"})
    if status == "awaiting_approval":
        d = _api(base, f"/api/incidents/{incident_id}")
        approvals = d.get("approvals") or []
        if approvals:
            _api(base, f"/api/incidents/{incident_id}/approvals/{approvals[0]['id']}/decision",
                 method="post", json={"decision": "approved", "comment": "eval"})
            status = _wait_status(base, incident_id, {"recovered", "needs_human"}, timeout_s=90)
    for p in procs:
        try:
            p.wait(timeout=5)
        except Exception:
            p.kill()
    obs = _api(base, f"/api/incidents/{incident_id}/runs/{run_id}/observation")
    return {"scenario": scenario, "round": round_no, "status": status,
            "elapsed": round(time.time() - t0, 1), "run_id": run_id,
            "observation": obs}


def aggregate(rounds: list) -> dict:
    n = len(rounds)
    recovered = sum(1 for r in rounds if r["status"] == "recovered")
    elapsed = [r["elapsed"] for r in rounds]
    in_tok = [i["detail"]["inputTokens"] for r in rounds for i in r["observation"]["timeline"]
              if i["type"] == "llm" and i["detail"].get("inputTokens")]
    out_tok = [i["detail"]["outputTokens"] for r in rounds for i in r["observation"]["timeline"]
               if i["type"] == "llm" and i["detail"].get("outputTokens")]
    tools = [i for r in rounds for i in r["observation"]["timeline"] if i["type"] == "tool"]
    anomaly_counts = {}
    for r in rounds:
        for a in r["observation"]["diagnosis"].get("anomalies", []):
            anomaly_counts[a["type"]] = anomaly_counts.get(a["type"], 0) + 1
    return {"success_rate": recovered / n if n else 0.0,
            "avg_elapsed": round(sum(elapsed) / n, 1) if n else 0.0,
            "avg_input_tokens": round(sum(in_tok) / len(in_tok), 1) if in_tok else 0.0,
            "avg_output_tokens": round(sum(out_tok) / len(out_tok), 1) if out_tok else 0.0,
            "avg_tool_calls": round(len(tools) / n, 1) if n else 0.0,
            "anomaly_counts": anomaly_counts}


def render_markdown(ts: str, rounds: list, stats: dict) -> str:
    lines = ["# TraceMind 真实模型评测报告(real_strict)", "",
             f"- 时间:{ts}", f"- 成功率:{stats['success_rate'] * 100:.0f}%",
             f"- 平均耗时:{stats['avg_elapsed']}s",
             f"- 平均 tokens:{stats['avg_input_tokens']}/{stats['avg_output_tokens']}(in/out)",
             f"- 平均工具调用:{stats['avg_tool_calls']} 次/轮",
             f"- 卡点分布:{stats['anomaly_counts'] or '无'}", "",
             "| 轮次 | 场景 | 终态 | 耗时 | 工具调用 |", "|---|---|---|---|---|"]
    for r in rounds:
        tools = sum(1 for i in r["observation"]["timeline"] if i["type"] == "tool")
        lines.append(f"| {r['round']} | {r['scenario']} | {r['status']} | {r['elapsed']}s | {tools} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    global _order_url, _order_host
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost:8000")
    p.add_argument("--order", default="http://localhost:8081")
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--out-dir", default="reports/evals")
    args = p.parse_args()
    _order_url = args.order
    _order_host = args.order.replace("http://", "").split(":")[0]
    rounds = []
    for scenario in ("SCN-001", "SCN-002"):
        for r in range(1, args.rounds + 1):
            print(f"[{scenario} round{r}] ...", flush=True)
            try:
                rounds.append(run_one_round(args.base, scenario, r))
            except requests.HTTPError as e:
                if "429" in str(e) or "quota" in str(e).lower():
                    print("额度/限流错误,停止。请核对额度或更换模型。", file=sys.stderr)
                    return 2
                raise
    stats = aggregate(rounds)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.out_dir) / f"agent-eval-{ts}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(ts, rounds, stats), encoding="utf-8")
    print(f"\n报告已写入 {out}")
    print(f"成功率 {stats['success_rate']*100:.0f}% 平均耗时 {stats['avg_elapsed']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
