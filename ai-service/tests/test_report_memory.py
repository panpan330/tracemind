import app.agent.memory as memory_mod
import app.agent.nodes as nodes
import app.agent.llm as llm_mod


def _fake_report_deps(monkeypatch, calls):
    monkeypatch.setattr(memory_mod, "record_case",
                        lambda state: calls.append(state.get("status")))
    fake_llm = type("L", (), {"write_report":
                              lambda self, s: {"content": "x", "root_cause_summary": "y"}})()
    monkeypatch.setattr(llm_mod, "get_llm", lambda: fake_llm)
    monkeypatch.setattr(nodes.postmortem_repo, "create_postmortem", lambda **k: None)
    monkeypatch.setattr(nodes.event_repo, "append_event", lambda *a, **k: None)
    monkeypatch.setattr(nodes.incident_repo, "update_state", lambda *a, **k: None)


def test_report_calls_record_case_on_recovered(monkeypatch):
    calls = []
    _fake_report_deps(monkeypatch, calls)
    state = {"incident_id": 1, "run_id": 2, "status": "recovered", "evidence": [],
             "fix_execution": {}, "recovery": {}, "degraded": False}
    nodes.report(state)
    assert calls == ["recovered"]
