"""审计仓库单测:mock control_engine,不触 DB。"""
import app.repositories.model_call_repo as mcr
import app.repositories.retrieval_repo as rcr


class FakeEngine:
    def __init__(self):
        self.sqls = []

    def begin(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.sqls.append((sql, params))


def test_model_call_insert_sql(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(mcr, "control_engine", engine)
    mcr.insert(incident_id=1, run_id=1, node="hypothesize", mode="real_demo",
               provider="bailian", model="m", model_snapshot="m-snap",
               prompt_version="v1", prompt_hash="abc", tool_schema_version="v1",
               logical_call_id="lc1", attempts_json="[]", finish_reason="stop",
               structured_output_valid=True, tool_call_count=0,
               provider_request_id="pr1", fallback_executor="",
               input_snapshot_json="{}", latency_ms=10, input_tokens=5,
               output_tokens=3, status="ok", error_code="", degraded=False,
               git_commit_sha="abc", knowledge_chunk_ids="[]")
    sql, params = engine.sqls[0]
    assert "INSERT INTO model_call" in str(sql)
    assert params[0] == 1


def test_retrieval_insert_sql(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(rcr, "control_engine", engine)
    rcr.insert(incident_id=1, run_id=1, node="hypothesize", query_text_hash="h",
               collection_alias="alias", collection_version="v1",
               embedding_model="text-embedding-v4", dimensions=1024,
               candidate_top_k=6, final_chunk_ids="[]", scores="[]",
               latency_ms=5, status="ok", error_code="", degraded=False)
    sql, params = engine.sqls[0]
    assert "INSERT INTO retrieval_record" in str(sql)
