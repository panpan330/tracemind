import app.agent.memory as mem


def test_case_text_contains_fingerprint():
    state = {"description": "库存查询慢", "run_id": 7,
             "evidence": [{"id": "E1", "passed": True, "key": "E1"}],
             "root_cause_code": "INDEX_MISSING", "root_cause": "缺少联合索引",
             "fix_execution": {"status": "succeeded"},
             "recovery": {"status": "recovered"}}
    text = mem._case_text(state)
    assert "库存查询慢" in text
    assert "INDEX_MISSING" in text
    assert "recovered" in text


def test_case_payload_fields():
    state = {"run_id": 7, "root_cause_code": "INDEX_MISSING",
             "fault_category": "SCN-001", "description": "库存慢",
             "evidence": [], "fix_execution": {"status": "succeeded"},
             "recovery": {"status": "recovered"}}
    payload = mem._case_payload(state)
    assert payload["root_cause_code"] == "INDEX_MISSING"
    assert payload["fault_category"] == "SCN-001"
    assert payload["recovered"] is True
    assert payload["run_id"] == 7
    assert payload["doc_id"] == "case-7"
    assert payload["title"] == "历史诊断案例"
    assert "INDEX_MISSING" in payload["text"]   # 案例文本供检索后注入


def test_record_case_skips_non_recovered(monkeypatch):
    calls = []
    monkeypatch.setattr(mem, "_upsert", lambda *a, **k: calls.append(1))
    mem.record_case({"status": "needs_human"})
    assert calls == []


def test_record_case_skips_when_embed_fails(monkeypatch):
    import app.rag.embedder as embedder_mod

    class FakeEmbedder:
        def embed(self, text):
            return None
    monkeypatch.setattr(embedder_mod, "Embedder", lambda: FakeEmbedder())
    calls = []
    monkeypatch.setattr(mem, "_upsert", lambda *a, **k: calls.append(1))
    mem.record_case({"status": "recovered", "description": "x", "evidence": [],
                     "root_cause_code": "INDEX_MISSING", "root_cause": "r",
                     "fix_execution": {"status": "succeeded"},
                     "recovery": {"status": "recovered"}, "run_id": 1})
    assert calls == []   # embedding 失败不沉淀


class _FakeStore:
    def __init__(self):
        self.upserts = []

    def upsert(self, point_id, vector, payload):
        self.upserts.append({"point_id": point_id, "vector": vector, "payload": payload})


def test_record_case_skips_non_reflection_failure():
    """human_approval rejected(非反思失败)不沉淀失败案例。"""
    state = {"run_id": 9, "status": "needs_human",
             "termination_reason": "approval_rejected",
             "reflection_count": 0, "root_cause_code": "X"}
    store = _FakeStore()
    mem.record_case(state, store=store)
    assert store.upserts == []


def test_record_case_reflection_exhausted_sinks_failure():
    """反思用尽仍未恢复 → 沉淀 recovered=False 案例。"""
    state = {"run_id": 10, "status": "needs_human",
             "termination_reason": "reflection_exhausted",
             "reflection_count": 3, "root_cause_code": "INDEX_MISSING",
             "fault_category": "SCN-001", "description": "库存慢",
             "reflection_log": [{"attempt_no": 1, "new_hypothesis": "连接池耗尽"}],
             "evidence": [], "fix_execution": {"status": "failed"}}
    store = _FakeStore()
    mem.record_case(state, store=store)
    assert len(store.upserts) == 1
    payload = store.upserts[0]["payload"]
    assert payload["recovered"] is False
    assert payload["doc_id"] == "case-10-fail"
    assert "reflection_exhausted" in payload["text"]
