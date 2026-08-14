from app.agent import llm as llm_mod


class _FakeRetriever:
    def __init__(self, hits):
        self._hits = hits

    def search(self, query, top_k=3):
        return self._hits


class _FakeClient:
    def chat(self, messages, max_tokens=600, model=None, tools=None):
        return None


def test_rag_context_includes_case_reference(monkeypatch):
    monkeypatch.setattr(llm_mod.retrieval_repo, "insert", lambda **k: None)
    llm = llm_mod.OpenAICompatibleLLM(
        client=_FakeClient(), strict=True,
        retriever=_FakeRetriever([]),   # runbook 空
        case_retriever=_FakeRetriever([{"doc_id": "case-7", "title": "历史案例",
                                        "text": "缺联合索引", "score": 0.9}]))
    rag = llm._rag_context({"description": "库存慢", "incident_id": 1, "run_id": 1})
    assert "case_reference" in rag
    assert "历史案例" in rag


def test_rag_context_empty_when_both_none(monkeypatch):
    monkeypatch.setattr(llm_mod.retrieval_repo, "insert", lambda **k: None)
    llm = llm_mod.OpenAICompatibleLLM(client=_FakeClient(), strict=True,
                                      retriever=None, case_retriever=None)
    rag = llm._rag_context({"description": "库存慢", "incident_id": 1, "run_id": 1})
    assert rag == ""


def test_rag_context_case_failure_not_blocking(monkeypatch):
    monkeypatch.setattr(llm_mod.retrieval_repo, "insert", lambda **k: None)

    class _Boom:
        def search(self, query, top_k=3):
            raise RuntimeError("qdrant down")

    llm = llm_mod.OpenAICompatibleLLM(client=_FakeClient(), strict=True,
                                      retriever=None, case_retriever=_Boom())
    rag = llm._rag_context({"description": "库存慢", "incident_id": 1, "run_id": 1})
    assert rag == ""   # 案例检索失败不阻塞


def test_case_references_marks_failure(monkeypatch):
    monkeypatch.setattr(llm_mod.retrieval_repo, "insert", lambda **k: None)
    llm = llm_mod.OpenAICompatibleLLM(
        client=_FakeClient(), strict=True, retriever=None,
        case_retriever=_FakeRetriever([{"doc_id": "case-10-fail", "title": "历史案例",
                                        "text": "失败案例(避坑):库存慢", "recovered": False}]))
    out = llm._case_references({"description": "库存慢"})
    assert 'recovered="false"' in out
    assert "失败案例(避坑)" in out
    assert "不要重复" in out


def test_case_references_success_keeps_original(monkeypatch):
    monkeypatch.setattr(llm_mod.retrieval_repo, "insert", lambda **k: None)
    llm = llm_mod.OpenAICompatibleLLM(
        client=_FakeClient(), strict=True, retriever=None,
        case_retriever=_FakeRetriever([{"doc_id": "case-7", "title": "历史案例",
                                        "text": "缺联合索引", "recovered": True}]))
    out = llm._case_references({"description": "库存慢"})
    assert 'recovered="false"' not in out
    assert "历史案例" in out
