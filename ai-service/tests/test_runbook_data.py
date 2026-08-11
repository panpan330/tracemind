"""Runbook 解析/分块/Point ID 单测。"""
from pathlib import Path

from app.rag.runbook_data import (chunk_text, content_hash, load_all_runbooks,
                                  parse_runbook, point_id)


def test_chunk_text_splits_long():
    text = ("段1。" * 50) + "\n\n" + ("段2。" * 50)
    chunks = chunk_text(text, max_chars=100)
    assert len(chunks) >= 2
    assert all(len(c) <= 100 for c in chunks)


def test_parse_runbook_frontmatter(tmp_path):
    md = tmp_path / "mysql-missing-index.md"
    md.write_text(
        "---\ndoc_id: runbook-mysql-missing-index\ntitle: MySQL 缺索引\n"
        "doc_fault_category: slow-sql\ndoc_service: inventory\n"
        "doc_scenario_id: SCN-001\ndoc_version: 1.0\n---\n"
        "## 症状\n接口变慢\n",
        encoding="utf-8",
    )
    parsed = parse_runbook(md)
    assert parsed["doc_id"] == "runbook-mysql-missing-index"
    assert parsed["sections"] == [{"section": "症状", "text": "接口变慢"}]


def test_point_id_stable():
    assert point_id("d", "s", 0) == point_id("d", "s", 0)
    assert point_id("d", "s", 0) != point_id("d", "s", 1)


def test_load_all_runbooks_ten(tmp_path):
    for i in range(10):
        (tmp_path / f"rb-{i}.md").write_text(
            f"---\ndoc_id: rb-{i}\ntitle: t\ndoc_fault_category: c\ndoc_service: s\n"
            f"doc_scenario_id: SCN-001\ndoc_version: 1.0\n---\n## x\n文本\n", encoding="utf-8")
    rbs = load_all_runbooks(tmp_path)
    assert len(rbs) == 10
