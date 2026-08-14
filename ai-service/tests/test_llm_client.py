"""LLMClient 单测:mock httpx.post,不触网。"""
import httpx
import pytest

from app.agent.llm_client import LLMClient, prompt_hash


class FakeResp:
    def __init__(self, json_data, status=200):
        self._json = json_data
        self.status_code = status

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=httpx.Request("POST", "http://x"),
                                        response=httpx.Response(self.status_code))


def test_bailian_sends_enable_thinking(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return FakeResp({"choices": [{"message": {"role": "assistant", "content": '{"a":1}'}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    client = LLMClient(base_url="http://llm", api_key="k", model="m", provider="bailian")
    result = client.chat([{"role": "user", "content": "hi"}])
    assert captured["payload"]["enable_thinking"] is False
    assert result.content == '{"a":1}'


def test_generic_does_not_send_enable_thinking(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return FakeResp({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    client = LLMClient(base_url="http://llm", api_key="k", model="m", provider="generic")
    client.chat([{"role": "user", "content": "hi"}])
    assert "enable_thinking" not in captured["payload"]


def test_parses_tool_calls(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return FakeResp({"choices": [{"message": {"role": "assistant", "content": "",
                                                  "tool_calls": [{
                                                      "id": "call_1",
                                                      "function": {
                                                          "name": "get_service_metrics",
                                                          "arguments": '{"service_ref": "inventory-service"}',
                                                      }}]}}],
                          "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    monkeypatch.setattr(httpx, "post", fake_post)
    client = LLMClient(base_url="http://llm", api_key="k", model="m")
    result = client.chat([{"role": "user", "content": "x"}], tools=[{"type": "function"}])
    assert result.tool_calls[0].name == "get_service_metrics"
    assert result.tool_calls[0].arguments == {"service_ref": "inventory-service"}
    assert result.usage["input_tokens"] == 10


def test_bad_arguments_json_returns_empty_tool_calls(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return FakeResp({"choices": [{"message": {"role": "assistant", "content": "",
                                                  "tool_calls": [{
                                                      "id": "c1",
                                                      "function": {"name": "x", "arguments": "not-json"},
                                                  }]}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    client = LLMClient(base_url="http://llm", api_key="k", model="m")
    result = client.chat([])
    assert result.tool_calls == []


def test_retries_on_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            return FakeResp({"error": "rate"}, status=429)
        return FakeResp({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    client = LLMClient(base_url="http://llm", api_key="k", model="m")
    result = client.chat([])
    assert result.content == "ok"
    assert calls["n"] == 3


def test_no_retry_on_400(monkeypatch):
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        return FakeResp({"error": "bad"}, status=400)

    monkeypatch.setattr(httpx, "post", fake_post)
    client = LLMClient(base_url="http://llm", api_key="k", model="m")
    assert client.chat([]) is None
    assert calls["n"] == 1


def test_returns_none_after_retries_exhausted(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return FakeResp({"error": "boom"}, status=500)

    monkeypatch.setattr(httpx, "post", fake_post)
    client = LLMClient(base_url="http://llm", api_key="k", model="m")
    assert client.chat([]) is None


def test_chat_json_parses_content(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return FakeResp({"choices": [{"message": {"role": "assistant", "content": '{"k": 1}'}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    client = LLMClient(base_url="http://llm", api_key="k", model="m")
    assert client.chat_json([]) == {"k": 1}


def test_prompt_hash_stable_and_short():
    h1 = prompt_hash("库存查询变慢" * 10)
    h2 = prompt_hash("库存查询变慢" * 10)
    assert h1 == h2 and len(h1) == 16 and h1 != prompt_hash("other")


def test_extract_json_tolerates_markdown_fence():
    from app.agent.llm_client import LLMClient
    text = '```json\n{"ok": true}\n```'
    assert LLMClient._extract_json(text) == {"ok": True}


def test_extract_json_tolerates_prefix_text():
    from app.agent.llm_client import LLMClient
    text = '好的,结果如下: {"a": 1} 请查收。'
    assert LLMClient._extract_json(text) == {"a": 1}


def test_chat_fallback_on_retry_exhausted(monkeypatch):
    """V1.11:主模型 3 次 429 退避耗尽 → 切 fallback 模型重试成功。"""
    from app.config import settings
    monkeypatch.setattr(settings, "fallback_model", "deepseek-v4-flash-0731")
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json["model"])
        if len(calls) <= 3:
            return FakeResp({}, status=429)
        return FakeResp({"choices": [{"message": {"role": "assistant",
                                                  "content": "ok", "tool_calls": []},
                                      "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    monkeypatch.setattr(httpx, "post", fake_post)
    client = LLMClient(base_url="http://llm", api_key="k", model="qwen3.8-max")
    r = client.chat([{"role": "user", "content": "hi"}])
    assert r is not None
    assert r.content == "ok"
    assert calls[-1] == "deepseek-v4-flash-0731"   # 第 4 次用 fallback


def test_chat_fallback_not_used_when_same_model(monkeypatch):
    """V1.11:fallback 与主模型相同 → 不额外重试(仍 3 次退避后 None)。"""
    from app.config import settings
    monkeypatch.setattr(settings, "fallback_model", "qwen3.8-max")
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json["model"])
        return FakeResp({}, status=429)

    monkeypatch.setattr(httpx, "post", fake_post)
    client = LLMClient(base_url="http://llm", api_key="k", model="qwen3.8-max")
    r = client.chat([{"role": "user", "content": "hi"}])
    assert r is None
    assert len(calls) == 3   # 无 fallback 重试


def test_chat_fallback_not_used_when_unset(monkeypatch):
    """V1.11:fallback 未配置 → 行为与现状一致(3 次退避后 None)。"""
    from app.config import settings
    monkeypatch.setattr(settings, "fallback_model", "")
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json["model"])
        return FakeResp({}, status=429)

    monkeypatch.setattr(httpx, "post", fake_post)
    client = LLMClient(base_url="http://llm", api_key="k", model="qwen3.8-max")
    r = client.chat([{"role": "user", "content": "hi"}])
    assert r is None
    assert len(calls) == 3
