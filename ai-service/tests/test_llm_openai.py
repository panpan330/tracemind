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

    def chat_json_with_usage(self, messages, max_tokens=600, model=None):
        self.calls.append((messages, max_tokens))
        r = self.responses.pop(0) if self.responses else None
        return r, {}, None

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


def test_select_tool_llm_empty_falls_back_to_planner():
    """真实 LLM 连续不返回 tool_calls(偶发不稳定)→ 确定性 planner 兜底,不抛 ModelDegradedError。
    修复前:strict 模式抛 ModelDegradedError → collect_evidence llm_unavailable → needs_human
    (VM 真实模型验收偶发失败:incident llm_unavailable)。工具选择用 planner 不构成根因降级
    (根因判定在 diagnose 由确定性 policy 完成),只保证证据收集不因 LLM 输出波动中断。"""
    from app.agent.llm_client import ChatResult
    import app.tools  # noqa: F401  注册 TOOL_REGISTRY(llm_tool_schemas 依赖)
    client = StubClient([ChatResult(content=None), ChatResult(content=None),
                         ChatResult(content=None)])
    llm = OpenAICompatibleLLM(client=client, strict=True)
    out = llm.select_tool({"evidence": [], "evidence_gate": {}},
                          "prompt", {"get_service_metrics", "get_index_info"})
    assert isinstance(out, list)  # 不抛异常,planner 兜底
    assert len(client.calls) == 3


def test_get_llm_unknown_mode_raises(monkeypatch):
    from app.agent.llm import get_llm
    from app.config import settings
    monkeypatch.setattr(settings, "llm_mode", "bogus")
    with pytest.raises(ValueError):
        get_llm()


class StubRetriever:
    def __init__(self, hits):
        self._hits = hits
        self.queries = []

    def search(self, query, top_k=3):
        self.queries.append(query)
        return self._hits


def test_hypothesize_includes_rag_context(monkeypatch):
    # 审计写入隔离:不触真实 DB
    monkeypatch.setattr("app.agent.llm.retrieval_repo.insert", lambda **kw: None)
    client = StubClient([{"hypotheses": [{"description": "缺索引"}]}])
    retriever = StubRetriever([{"text": "EXPLAIN 显示全表扫描", "score": 0.9,
                                "doc_id": "runbook-mysql-missing-index", "title": "缺索引"}])
    llm = OpenAICompatibleLLM(client=client, retriever=retriever, strict=False)
    llm.hypothesize({"description": "库存查询变慢", "run_id": 1, "incident_id": 1})
    content = client.calls[0][0][0]["content"]
    assert "全表扫描" in content
    assert "<knowledge_reference" in content


def test_hypothesize_survives_retriever_failure(monkeypatch):
    monkeypatch.setattr("app.agent.llm.retrieval_repo.insert", lambda **kw: None)

    class BoomRetriever:
        def search(self, query, top_k=3):
            raise RuntimeError("qdrant down")

    client = StubClient([{"hypotheses": [{"description": "缺索引"}]}])
    llm = OpenAICompatibleLLM(client=client, retriever=BoomRetriever(), strict=False)
    hyps = llm.hypothesize({"description": "x", "run_id": 1, "incident_id": 1})
    assert hyps[0]["description"] == "缺索引"
