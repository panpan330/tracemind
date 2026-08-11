"""回归评测流水线:fast(不依赖外部服务)/ full(含真实模型与 E2E)。
任一阶段失败:标记后续 SKIPPED,统一返回非零退出码,仍生成报告(设计 V1.3 §9)。"""
import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from report_utils import collect_metadata

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports" / "regression"

STAGES_FAST = [
    ("pytest", "ai-service",
     ["uv", "run", "pytest", "tests/", "-m", "not integration and not e2e", "-q"]),
    ("eval_agent_fake", "ai-service",
     ["uv", "run", "python", "../scripts/eval_agent.py",
      "--mode", "offline", "--llm", "fake", "--runs", "1"]),
    ("eval_rag_schema", ".",
     ["python", "-c",
      "import json, pathlib; "
      "d = json.loads(pathlib.Path('data/retrieval_test_cases.json').read_text(encoding='utf-8')); "
      "assert len(d) >= 14 and all('query' in c and 'expected_doc_ids' in c for c in d); "
      "p = pathlib.Path('data/evaluation_policy.yaml'); assert p.exists(); "
      "print('RAG schema OK:', len(d), 'cases, policy frozen')"]),
]

STAGES_FULL = STAGES_FAST + [
    ("preflight", ".", ["python", "scripts/check_external_deps.py"]),
    ("smoke_llm", ".", ["python", "scripts/smoke_llm.py"]),
    ("eval_agent_real", "ai-service",
     ["uv", "run", "python", "../scripts/eval_agent.py",
      "--mode", "offline", "--llm", "real_strict", "--runs", "3"]),
    ("e2e_scn001", ".", ["python", "scripts/verify-m5.py"]),
    ("e2e_scn002", ".", ["python", "scripts/verify-m13-scn002.py"]),
]


def _run(name: str, cwd: str, cmd: list[str], results: list[dict]) -> bool:
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=ROOT / cwd)
    ok = proc.returncode == 0
    results.append({"stage": name, "ok": ok, "seconds": round(time.time() - t0, 1),
                    "skipped": False})
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["fast", "full"], default="fast")
    args = ap.parse_args()
    results: list[dict] = []
    meta = collect_metadata()
    REPORTS.mkdir(parents=True, exist_ok=True)

    stages = STAGES_FULL if args.tier == "full" else STAGES_FAST
    ok_all = True
    for name, cwd, cmd in stages:
        if ok_all:
            ok_all = _run(name, cwd, cmd, results) and ok_all
        else:
            results.append({"stage": name, "ok": False, "seconds": 0, "skipped": True})

    lines = ["# TraceMind Regression Report", "",
             f"- tier: {args.tier}", f"- time: {datetime.now().isoformat()}"]
    for k, v in meta.items():
        lines.append(f"- {k}: {v}")
    lines += ["", "| stage | result | seconds |", "|---|---|---|"]
    for r in results:
        res = "PASS" if r["ok"] else ("SKIPPED" if r["skipped"] else "FAIL")
        lines.append(f"| {r['stage']} | {res} | {r['seconds']} |")
    lines += ["", f"**exit_code: {0 if ok_all else 1}**"]
    out = REPORTS / f"regression-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告: {out} exit={0 if ok_all else 1}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
