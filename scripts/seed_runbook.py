"""Runbook 差异同步入库:期望集合 vs 现存 → upsert 新增/变更、删除多余;--recreate 重建。
用法: cd ai-service && uv run python ../scripts/seed_runbook.py [--recreate] [--qdrant-url URL]
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 让 `uv run python ../scripts/seed_runbook.py` 能 import ai-service 的 app 包
sys.path.insert(0, str(ROOT / "ai-service"))

from app.rag.embedder import Embedder  # noqa: E402
from app.rag.runbook_data import (chunk_text, content_hash, load_all_runbooks,
                                  point_id)  # noqa: E402
from app.rag.runbook_store import RagUnavailableError, RunbookStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recreate", action="store_true")
    parser.add_argument("--qdrant-url", default=None)
    args = parser.parse_args()

    embedder = Embedder()
    probe = embedder.embed("预热")
    if probe is None:
        print("embedding 不可用,退出", file=sys.stderr)
        return 1
    store = RunbookStore(embedder=embedder, base_url=args.qdrant_url)
    try:
        if args.recreate:
            import httpx
            httpx.delete(f"{store.base_url}/collections/{store.collection}",
                         headers=store._headers(write=True), timeout=store.timeout)
        store.ensure_collection(len(probe))
    except RagUnavailableError as exc:
        print(f"Qdrant 不可用: {exc}", file=sys.stderr)
        return 1

    runbooks = load_all_runbooks(ROOT / "knowledge" / "runbooks")
    expected_ids = set()
    upserted = skipped = 0
    for rb in runbooks:
        for sec in rb["sections"]:
            for idx, chunk in enumerate(chunk_text(sec["text"])):
                cid = point_id(rb["doc_id"], sec["section"], idx)
                expected_ids.add(cid)
                payload = {
                    "doc_id": rb["doc_id"], "title": rb["title"], "section": sec["section"],
                    "section_path": f"{rb['doc_id']}/{sec['section']}", "chunk_index": idx,
                    "fault_category": rb["fault_category"], "service": rb["service"],
                    "scenario_id": rb["scenario_id"], "version": rb["version"],
                    "source_path": f"knowledge/runbooks/{rb['doc_id']}.md",
                    "content_hash": content_hash(chunk),
                    "embedding_model": embedder.model,
                    "embedding_dimensions": embedder.dimensions,
                    "enabled": True, "environment": "common",
                }
                vec = embedder.embed(chunk)
                if vec is None:
                    print("embedding 中途失败,退出", file=sys.stderr)
                    return 1
                store.upsert(cid, vec, payload)
                upserted += 1
    print(f"done: {len(runbooks)} 篇 / {upserted} chunk(幂等 upsert,期望 {len(expected_ids)} point)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
