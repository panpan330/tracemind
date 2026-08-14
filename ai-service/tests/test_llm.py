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


def test_hypothesize_uses_routed_model(monkeypatch):
    """V1.11:配置了 hypothesize_model 时,节点调用传给 client 的 model 是路由值。"""
    from app.agent.llm import OpenAICompatibleLLM
    from app.config import settings
    monkeypatch.setattr(settings, "hypothesize_model", "qwen3.8-max")
    captured = {}

    class _Client:
        def chat_json_with_usage(self, messages, max_tokens=600, model=None):
            captured["model"] = model
            return ({"hypotheses": [{"description": "h"}]},
                    {"input_tokens": 10, "output_tokens": 5}, "stop")

    llm = OpenAICompatibleLLM(client=_Client(), strict=True)
    llm.hypothesize({"description": "库存慢", "incident_id": 1, "run_id": 1})
    assert captured["model"] == "qwen3.8-max"


def test_select_tool_uses_routed_model(monkeypatch):
    from app.agent.llm import OpenAICompatibleLLM
    from app.config import settings
    monkeypatch.setattr(settings, "select_tool_model", "qwen3.7-flash")
    captured = {}

    class _Client:
        def chat(self, messages, max_tokens=600, model=None, tools=None):
            captured["model"] = model
            from app.agent.llm_client import ChatResult
            return ChatResult(content="", tool_calls=[], finish_reason="tool_calls",
                              usage={}, model=model)

    monkeypatch.setattr(
        "app.mcp.contract.llm_tool_schemas",
        lambda: [{"type": "function", "function": {
            "name": "get_index_info", "description": "",
            "parameters": {"type": "object", "properties": {}}}}])
    llm = OpenAICompatibleLLM(client=_Client(), strict=True)
    llm.select_tool({"description": "库存慢", "incident_id": 1, "run_id": 1},
                    "prompt", {"get_index_info"})
    assert captured["model"] == "qwen3.7-flash"
