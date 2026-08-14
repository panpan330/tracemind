import app.services.observation_service as obs


def _llm(**kw):
    base = {"node": "hypothesize", "latency_ms": 100, "input_tokens": 10,
            "output_tokens": 5, "attempts_json": "[]", "fallback_executor": "",
            "structured_output_valid": 1, "knowledge_chunk_ids": ""}
    base.update(kw)
    return base


def _tool(**kw):
    base = {"tool_name": "get_trace", "transport": "mcp_streamable_http",
            "attempt_no": 1, "outcome": "completed", "error_code": None,
            "latency_ms": 200, "trace_id": "t1"}
    base.update(kw)
    return base


def test_anomaly_duplicate_tool_call(monkeypatch):
    monkeypatch.setattr(obs, "list_model_calls_by_run", lambda r: [])
    monkeypatch.setattr(obs, "list_retrievals_by_run", lambda r: [])
    monkeypatch.setattr(obs, "list_tool_call_attempts_by_run",
                        lambda r: [_tool(), _tool(attempt_no=2, trace_id="t2")])
    monkeypatch.setattr(obs, "_run_summary", lambda i, r: {"status": "needs_human",
                                                           "terminationReason": "no_progress"})
    out = obs.build_run_observation(1, 1)
    types = [a["type"] for a in out["diagnosis"]["anomalies"]]
    assert "duplicate_tool_call" in types


def test_anomaly_retry_and_fallback(monkeypatch):
    monkeypatch.setattr(obs, "list_model_calls_by_run",
                        lambda r: [_llm(attempts_json='[{"n":1},{"n":2}]',
                                        fallback_executor="deterministic")])
    monkeypatch.setattr(obs, "list_retrievals_by_run", lambda r: [])
    monkeypatch.setattr(obs, "list_tool_call_attempts_by_run", lambda r: [])
    monkeypatch.setattr(obs, "_run_summary", lambda i, r: {"status": "recovered",
                                                           "terminationReason": None})
    out = obs.build_run_observation(1, 1)
    types = [a["type"] for a in out["diagnosis"]["anomalies"]]
    assert "retry" in types
    assert "fallback_triggered" in types
