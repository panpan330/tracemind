"""检索评测:calibrate(校准阈值)与 eval(冻结阈值正式评测)分离。
用法: cd ai-service && uv run python ../scripts/eval_rag.py --phase calibrate
      cd ai-service && uv run python ../scripts/eval_rag.py --phase eval
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "data" / "evaluation_policy.yaml"

# 让 `uv run python ../scripts/eval_rag.py` 能 import ai-service 的 app 包
sys.path.insert(0, str(ROOT / "ai-service"))

from app.rag.embedder import Embedder  # noqa: E402
from app.rag.retriever import Retriever  # noqa: E402
from app.rag.runbook_store import RunbookStore  # noqa: E402


def _load(name: str) -> list[dict]:
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


def _collect_scores(retriever, cases) -> tuple[list[float], list[float]]:
    relevant, irrelevant = [], []
    for c in cases:
        hits = retriever.search(c["query"], top_k=6)
        top = hits[0]["score"] if hits else 0.0
        (relevant if c["relevance"] == "relevant" else irrelevant).append(top)
    return relevant, irrelevant


def calibrate(retriever) -> int:
    cases = _load("retrieval_calibration_cases.json")
    rel, irr = _collect_scores(retriever, cases)
    rel_p50 = statistics.median(rel) if rel else 0.0
    irr_max = max(irr) if irr else 0.0
    print(f"相关分数: min={min(rel) if rel else 0:.3f} p50={rel_p50:.3f} max={max(rel) if rel else 0:.3f}")
    print(f"无关分数: min={min(irr) if irr else 0:.3f} p50={statistics.median(irr) if irr else 0:.3f} max={irr_max:.3f}")
    print("人工确认后把阈值写入 data/evaluation_policy.yaml 与 TRACEMIND_RAG_SCORE_THRESHOLD")
    print(f"建议阈值区间: ({irr_max:.3f}, {rel_p50:.3f}]")
    return 0


def evaluate(retriever) -> int:
    cases = _load("retrieval_test_cases.json")
    if POLICY.exists():
        threshold = float(POLICY.read_text(encoding="utf-8")
                          .split("score_threshold:")[1].split()[0])
    else:
        threshold = 0.0
    hit3 = mrr = 0.0
    rel_total = irr_total = rel_empty = irr_reject = 0
    latencies = []
    for c in cases:
        start = time.monotonic()
        hits = retriever.search(c["query"], top_k=3)
        latencies.append(int((time.monotonic() - start) * 1000))
        ids = [h["doc_id"] for h in hits if h["score"] >= threshold]
        if c["relevance"] == "relevant":
            rel_total += 1
            if not ids:
                rel_empty += 1
            expected = c["expected_doc_ids"][0]
            if expected in ids:
                hit3 += 1
                mrr += 1.0 / (ids.index(expected) + 1)
        else:
            irr_total += 1
            if not ids:
                irr_reject += 1
    n = rel_total or 1
    p95_idx = max(0, int(len(latencies) * 0.95) - 1)
    print(f"Hit@3: {hit3 / n:.0%}(≥80%)")
    print(f"MRR: {mrr / n:.3f}(≥0.7)")
    print(f"相关无结果率: {rel_empty}/{rel_total} = {rel_empty / n:.0%}")
    print(f"无关拒绝率: {irr_reject}/{irr_total} = {irr_reject / irr_total:.0%}")
    print(f"延迟 P50/P95: {statistics.median(latencies) if latencies else 0}/"
          f"{sorted(latencies)[p95_idx] if latencies else 0}ms")
    ok = hit3 / n >= 0.8 and mrr / n >= 0.7
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["calibrate", "eval"], default="eval")
    parser.add_argument("--qdrant-url", default=None)
    args = parser.parse_args()
    retriever = Retriever(RunbookStore(embedder=Embedder(), base_url=args.qdrant_url))
    return calibrate(retriever) if args.phase == "calibrate" else evaluate(retriever)


if __name__ == "__main__":
    sys.exit(main())
