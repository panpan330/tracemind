"""Task 3.3: FakeLLM 与 LLM 节点的确定性测试(不调网络)。"""
import hashlib
import json

from app.agent.llm import FakeLLM, get_llm
from app.config import settings


def test_fake_llm_hypothesize_returns_deterministic_hypotheses():
    llm = FakeLLM()
    hyps = llm.hypothesize({"severity": "high", "service_ref": "inventory-service"})
    assert isinstance(hyps, list) and len(hyps) >= 1
    assert hyps[0]["id"] == "h1"
    assert "缺少联合索引" in hyps[0]["description"]
    assert hyps[0]["status"] == "proposed"


def test_fake_llm_propose_fix_has_parameters_hash():
    llm = FakeLLM()
    fix = llm.propose_fix({"confirmed_hypothesis_id": "h1"})
    assert fix["action_type"] == "CREATE_INVENTORY_INDEX"
    assert fix["risk_level"] in ("low", "medium", "high")
    params = fix["parameters"]
    expected_hash = hashlib.sha256(
        json.dumps(params, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    assert fix["parameters_hash"] == expected_hash
    assert len(fix["parameters_hash"]) == 64


def test_fake_llm_write_report_uses_only_persisted_facts():
    llm = FakeLLM()
    state = {
        "confirmed_hypothesis_id": "h1",
        "evidence": [
            {"id": "E1", "source": "get_service_metrics", "content": {"p95Ms": 120}, "passed": True},
            {"id": "E5", "source": "get_index_info", "content": {"indexes": ["PRIMARY"]}, "passed": True},
        ],
        "recovery": {"status": "recovered", "latency_p95_after": 3},
        "fix_execution": {"status": "succeeded"},
    }
    report = llm.write_report(state)
    assert "根因" in report["content"]
    assert "缺少联合索引" in report["content"]
    assert "recovered" in report["content"] or "恢复" in report["content"]


def test_get_llm_respects_llm_mode_config():
    settings.llm_mode = "fake"
    llm = get_llm()
    assert isinstance(llm, FakeLLM)
