"""离线 Agent 评测:Fixture 注入 + 进程内跑图 + 自动审批(Command resume)。
用法: cd ai-service && uv run python ../scripts/eval_agent.py --mode offline --llm fake --runs 1
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "data" / "eval_cases"
AI_SERVICE = ROOT / "ai-service"
TERMINAL = {"recovered", "needs_human", "rejected", "failed"}

# 让 `uv run python ../scripts/eval_agent.py` 能 import ai-service 的 app 包
sys.path.insert(0, str(AI_SERVICE))


def run_offline(case: dict, thread_id: str) -> dict:
    from app.agent.graph import build_graph
    from app.tools.execute import set_eval_fixture
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    set_eval_fixture(case["tool_fixtures"])
    state = {
        "incident_id": 0, "run_id": 0, "title": case["title"],
        "description": case["description"], "severity": case["severity"],
        "service_ref": "inventory-service", "status": "investigating",
        "hypotheses": [], "evidence": [], "evidence_gate": {},
        "decision_attempt_count": 0, "tool_execution_count": 0,
        "consecutive_invalid_count": 0, "consecutive_no_progress_count": 0,
    }
    graph = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": thread_id}}
    result = None
    for _ in range(30):  # 处理 interrupt(EvalApprover:自动批准)
        try:
            if result is None:
                out = graph.invoke(state, config=config)
            else:
                out = graph.invoke(Command(resume={"decision": "approved"}), config=config)
        except Exception as exc:  # noqa: BLE001
            return {"terminal_status": "failed", "root_cause": "error",
                    "failure_reason": str(exc)}
        result = out
        # 离线评测只验证根因与提案:propose_fix 产出正确提案即通过
        # (execute_fix/verify_recovery 的真实闭环由 E2E 冒烟验证,fixture 不覆盖)
        if (out.get("fix_proposal") or {}).get("action_type") == "CREATE_INVENTORY_INDEX":
            return {"terminal_status": "awaiting_approval", "root_cause": "missing_index",
                    "evidence_gate": out.get("evidence_gate")}
        if out.get("status") in TERMINAL:
            break
    proposal = result.get("fix_proposal") or {}
    root = ("missing_index" if result.get("status") == "recovered"
            and proposal.get("action_type") == "CREATE_INVENTORY_INDEX"
            else "needs_human")
    return {"terminal_status": result.get("status"), "root_cause": root,
            "evidence_gate": result.get("evidence_gate")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["offline"], default="offline")
    parser.add_argument("--llm", choices=["fake", "real_strict"], default="fake")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--case-filter", default="")
    args = parser.parse_args()

    # 必须在 import app.* 之前设置:settings 模块级单例读取一次 env
    os.environ["TRACEMIND_LLM_MODE"] = args.llm
    os.environ["TRACEMIND_RAG_MODE"] = "off"  # 离线评测使用 Fixture,不依赖 RAG/Qdrant
    from app.config import settings  # noqa: E402 读 .env.local(TRACEMIND_EVAL_CHAT_MODEL)
    if args.llm == "real_strict" and not settings.eval_chat_model:
        print("--llm real_strict 需要 TRACEMIND_EVAL_CHAT_MODEL(在 .env.local 配置)", file=sys.stderr)
        return 1

    cases = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(CASES_DIR.glob("*.json"))]
    if args.case_filter:
        cases = [c for c in cases if c["case_id"] == args.case_filter]

    pos_ok = pos_total = neg_bad = neg_total = 0
    for case in cases:
        for rep in range(args.runs):
            actual = run_offline(case, thread_id=f"eval-{case['case_id']}-{rep}")
            passed = (actual["root_cause"] == case["expected"])
            print(f"[{case['case_id']}] run{rep + 1}: expected={case['expected']} "
                  f"actual={actual['root_cause']} {'PASS' if passed else 'FAIL'}")
            if case["expected"] == "missing_index":
                pos_total += 1
                pos_ok += 1 if passed else 0
            else:
                neg_total += 1
                neg_bad += 1 if not passed else 0
    recall = pos_ok / pos_total if pos_total else 1.0
    err_rate = neg_bad / neg_total if neg_total else 0.0
    print(f"正例根因召回率: {pos_ok}/{pos_total} = {recall:.0%}(≥80%)")
    print(f"负例错误修复率: {neg_bad}/{neg_total} = {err_rate:.0%}(=0%)")
    ok = recall >= 0.8 and err_rate == 0.0
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
