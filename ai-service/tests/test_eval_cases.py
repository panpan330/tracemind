"""评测集结构校验。"""
import json
from pathlib import Path

CASES_DIR = Path(__file__).resolve().parents[2] / "data" / "eval_cases"


def test_16_cases_schema():
    """V1.3:动态 N/N(24 条 = 16 SCN-001 + 8 SCN-002),双根因 expected。"""
    files = sorted(CASES_DIR.glob("*.json"))
    assert len(files) == 24
    expected_set = set()
    for f in files:
        case = json.loads(f.read_text(encoding="utf-8"))
        for field in ("case_id", "title", "description", "expected", "severity", "tool_fixtures"):
            assert case.get(field), f"{f.name} 缺 {field}"
        assert case["expected"] in {"MISSING_INVENTORY_INDEX", "needs_human",
                                    "LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION"}
        assert case["severity"] in {"low", "medium", "high"}
        # fixture key = tool_name:canonical_args_hash 格式(至少一个)
        assert len(case["tool_fixtures"]) >= 1
        assert expected_set.isdisjoint({case["case_id"]})
        expected_set.add(case["case_id"])


def test_coverage_matrix():
    files = sorted(CASES_DIR.glob("*.json"))
    cases = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    by_expected = {c["expected"] for c in cases}
    assert "MISSING_INVENTORY_INDEX" in by_expected and "needs_human" in by_expected
    assert "LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION" in by_expected
    pos = sum(1 for c in cases if c["expected"] != "needs_human")
    neg = sum(1 for c in cases if c["expected"] == "needs_human")
    assert pos == 7 and neg == 17   # 7 正例(4 SCN-001 + 2 锁 + 1 INDEX-ONLY)+ 17 负例


RAG_CAL = Path(__file__).resolve().parents[2] / "data" / "retrieval_calibration_cases.json"
RAG_TEST = Path(__file__).resolve().parents[2] / "data" / "retrieval_test_cases.json"


def test_rag_case_schemas():
    cal = json.loads(RAG_CAL.read_text(encoding="utf-8"))
    tst = json.loads(RAG_TEST.read_text(encoding="utf-8"))
    assert len(cal) >= 6 and len(tst) >= 8
    for c in cal + tst:
        assert c.get("query")
        assert "expected_doc_ids" in c
        if c["relevance"] == "relevant":
            assert c.get("expected_doc_ids")   # 相关用例必须标注预期文档
        assert c.get("relevance") in {"relevant", "irrelevant"}   # 校准集需要相关/无关标注
    # 测试集不含校准集 query(避免泄漏)
    cal_q = {c["query"] for c in cal}
    assert all(c["query"] not in cal_q for c in tst)
