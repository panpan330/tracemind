"""状态扩展:degraded 属性与 report 阶段失败语义。"""
from app.agent.nodes import report
from app.agent.state import IncidentState


def base_state(**overrides):
    state: IncidentState = {
        "incident_id": 1, "run_id": 1, "status": "recovered",
        "description": "x", "evidence": [],
        "fix_execution": {"status": "succeeded"}, "recovery": {"status": "recovered"},
    }
    state.update(overrides)
    return state


def test_report_failure_keeps_recovered(monkeypatch):
    calls = {"degraded_events": []}
    monkeypatch.setattr("app.agent.nodes.postmortem_repo.create_postmortem",
                        lambda *a, **kw: None)
    monkeypatch.setattr("app.agent.nodes.event_repo.append_event", lambda *a, **kw: None)

    class BoomLLM:
        def write_report(self, state):
            raise RuntimeError("model down")

    monkeypatch.setattr("app.agent.nodes._emit_degradation",
                        lambda state, kind: calls["degraded_events"].append(kind))
    out = report(base_state(), llm=BoomLLM())
    assert out["report"]["status"] == "failed"
    assert out["degraded"] is True
    assert "report_generation_failed" in out["degradation_reasons"]


def test_report_success_sets_ready(monkeypatch):
    monkeypatch.setattr("app.agent.nodes.postmortem_repo.create_postmortem",
                        lambda *a, **kw: None)
    monkeypatch.setattr("app.agent.nodes.event_repo.append_event", lambda *a, **kw: None)

    class OkLLM:
        def write_report(self, state):
            return {"content": "# 复盘", "root_cause_summary": "缺索引"}

    out = report(base_state(), llm=OkLLM())
    assert out["report"]["status"] == "ready"
    assert out["degraded"] is False
