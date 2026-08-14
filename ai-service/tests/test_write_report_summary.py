from app.agent import llm as llm_mod


def test_write_report_uses_summarized_evidence(monkeypatch):
    captured = {}
    monkeypatch.setattr(llm_mod, "summarize",
                        lambda ev, max_keep=8: captured.setdefault("called", True) or ev)
    monkeypatch.setattr(llm_mod.OpenAICompatibleLLM, "_audit_model_call",
                        lambda self, *a, **k: None)

    class C:
        def chat_json_with_usage(self, messages, max_tokens=600, model=None):
            return {"content": "ok", "root_cause_summary": "r"}, \
                   {"input_tokens": 1, "output_tokens": 1}, "stop"

    l = llm_mod.OpenAICompatibleLLM(client=C(), strict=False,
                                    retriever=None, case_retriever=None)
    l.write_report({"description": "d", "evidence": [], "fix_execution": {},
                    "recovery": {}, "degraded": False, "incident_id": 1, "run_id": 1})
    assert captured.get("called") is True
