"""OpenAICompatibleLLM 单测:mock LLMClient,不触网。
select_tool 行为测试在 T5(tool_calling)落地后补充;本文件聚焦 hypothesize/write_report。"""
import pytest

from app.agent.llm import ModelDegradedError, OpenAICompatibleLLM


class StubClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_json(self, messages, max_tokens=600, model=None):
        self.calls.append((messages, max_tokens))
        return self.responses.pop(0) if self.responses else None

    def chat(self, messages, tools=None, max_tokens=600, model=None):
        self.calls.append((messages, max_tokens))
        r = self.responses.pop(0) if self.responses else None
        return r


def test_hypothesize_parses_structured_output():
    client = StubClient([{"hypotheses": [{"description": "缺少联合索引"}]}])
    llm = OpenAICompatibleLLM(client=client, strict=True)
    hyps = llm.hypothesize({"description": "库存查询变慢"})
    assert hyps[0]["description"] == "缺少联合索引"
    assert "库存查询变慢" in client.calls[0][0][0]["content"]


def test_hypothesize_retries_bad_structure():
    client = StubClient([{"bad": 1}, None, {"hypotheses": [{"description": "ok"}]}])
    llm = OpenAICompatibleLLM(client=client, strict=True)
    assert llm.hypothesize({"description": "x"})[0]["description"] == "ok"
    assert len(client.calls) == 3


def test_strict_raises_on_total_failure():
    llm = OpenAICompatibleLLM(client=StubClient([None, None, None]), strict=True)
    with pytest.raises(ModelDegradedError):
        llm.hypothesize({"description": "x"})


def test_demo_falls_back_to_template():
    llm = OpenAICompatibleLLM(client=StubClient([None, None, None]), strict=False)
    hyps = llm.hypothesize({"description": "x"})
    assert hyps and hyps[0]["description"]


def test_write_report_strict_failure_raises():
    llm = OpenAICompatibleLLM(client=StubClient([None, None, None]), strict=True)
    with pytest.raises(ModelDegradedError):
        llm.write_report({"evidence": []})


def test_write_report_demo_falls_back_to_template():
    llm = OpenAICompatibleLLM(client=StubClient([None, None, None]), strict=False)
    report = llm.write_report({"evidence": [], "fix_execution": {"status": "succeeded"},
                               "recovery": {"status": "recovered"}})
    assert report["content"]


def test_get_llm_unknown_mode_raises(monkeypatch):
    from app.agent.llm import get_llm
    from app.config import settings
    monkeypatch.setattr(settings, "llm_mode", "bogus")
    with pytest.raises(ValueError):
        get_llm()
