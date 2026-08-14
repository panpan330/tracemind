"""LLM 调用审计接线:验证 hypothesize/select_tool/write_report 会写 model_call。"""
from app.agent import llm as llm_mod
from app.agent.llm_client import ChatResult
from app.repositories import model_call_repo, retrieval_repo


class _FakeClient:
    def __init__(self, content=None, tool_calls=None):
        self._content = content
        self._tool_calls = tool_calls or []

    def chat_json_with_usage(self, messages, max_tokens=600, model=None):
        import json
        import re
        m = re.search(r"\{.*\}", self._content or "", re.DOTALL)
        data = json.loads(m.group(0)) if m else None
        return data, {"input_tokens": 100, "output_tokens": 20}, "stop"

    def chat(self, messages, tools=None, max_tokens=600, model=None):
        return ChatResult(content=self._content, tool_calls=self._tool_calls,
                          finish_reason="stop",
                          usage={"input_tokens": 100, "output_tokens": 20},
                          model="qwen-test")


class _FakeRetriever:
    def search(self, *a, **k):
        return []


def _mk_llm(client, strict=True):
    return llm_mod.OpenAICompatibleLLM(client=client, strict=strict, retriever=_FakeRetriever())


def _silence_retrieval(monkeypatch):
    monkeypatch.setattr(retrieval_repo, "insert", lambda **kw: None)


def test_hypothesize_records_model_call(monkeypatch):
    recorded = {}

    def fake_insert(**kw):
        recorded.update(kw)
    monkeypatch.setattr(model_call_repo, "insert", fake_insert)
    _silence_retrieval(monkeypatch)

    l = _mk_llm(_FakeClient(content='{"hypotheses":[{"description":"缺联合索引"}]}'))
    l.hypothesize({"incident_id": 1, "run_id": 2, "description": "慢查询"})

    assert recorded["node"] == "hypothesize"
    assert recorded["input_tokens"] == 100
    assert recorded["output_tokens"] == 20
    assert recorded["structured_output_valid"] is True
    assert recorded["fallback_executor"] == ""


def test_hypothesize_records_fallback(monkeypatch):
    recorded = {}

    def fake_insert(**kw):
        recorded.update(kw)
    monkeypatch.setattr(model_call_repo, "insert", fake_insert)
    _silence_retrieval(monkeypatch)

    # 返回非法 JSON → 结构化输出无效 → 走兜底(strict=False 下兜底是正常返回路径)
    l = _mk_llm(_FakeClient(content="不是 JSON"), strict=False)
    l.hypothesize({"incident_id": 1, "run_id": 2, "description": "慢查询"})

    assert recorded["node"] == "hypothesize"
    assert recorded["structured_output_valid"] is False
    assert recorded["fallback_executor"] != ""


def test_hypothesize_records_routed_model(monkeypatch):
    """V1.11:路由配置时,审计记录实际用模型;正常路径 degraded=False。"""
    from app.config import settings
    monkeypatch.setattr(settings, "hypothesize_model", "qwen3.8-max")
    recorded = {}

    def fake_insert(**kw):
        recorded.update(kw)
    monkeypatch.setattr(model_call_repo, "insert", fake_insert)
    _silence_retrieval(monkeypatch)

    l = _mk_llm(_FakeClient(content='{"hypotheses":[{"description":"缺联合索引"}]}'))
    l.hypothesize({"incident_id": 1, "run_id": 2, "description": "慢查询"})

    assert recorded["node"] == "hypothesize"
    assert recorded["model"] == "qwen3.8-max"
    assert recorded["degraded"] is False


def test_hypothesize_records_fallback_degraded(monkeypatch):
    """V1.11:fallback 场景(实际模型 ≠ 路由模型)→ 审计 degraded=True + 实际模型。"""
    from app.config import settings
    monkeypatch.setattr(settings, "hypothesize_model", "qwen3.8-max")

    class _FallbackClient(_FakeClient):
        def __init__(self):
            super().__init__(content='{"hypotheses":[{"description":"缺联合索引"}]}')

        def chat_json_with_usage(self, messages, max_tokens=600, model=None):
            import json
            import re
            m = re.search(r"\{.*\}", self._content or "", re.DOTALL)
            data = json.loads(m.group(0)) if m else None
            return data, {"input_tokens": 100, "output_tokens": 20}, "stop", "deepseek-v4-flash-0731"

    recorded = {}

    def fake_insert(**kw):
        recorded.update(kw)
    monkeypatch.setattr(model_call_repo, "insert", fake_insert)
    _silence_retrieval(monkeypatch)

    l = _mk_llm(_FallbackClient())
    l.hypothesize({"incident_id": 1, "run_id": 2, "description": "慢查询"})

    assert recorded["model"] == "deepseek-v4-flash-0731"
    assert recorded["degraded"] is True
