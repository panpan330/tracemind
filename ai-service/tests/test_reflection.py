"""V1.10 反思循环:state 字段、reflect 节点、graph 条件边。"""
from app.agent.state import IncidentState, append_records


def test_state_has_reflection_fields():
    assert "reflection_log" in IncidentState.__annotations__
    assert "reflection_count" in IncidentState.__annotations__
    s = IncidentState(
        incident_id=1, run_id=1, title="t", description="d", status="needs_human",
        root_cause_code="X", created_at="2026-01-01T00:00:00",
        reflection_log=[{"attempt_no": 1, "new_hypothesis": "h"}], reflection_count=1,
    )
    assert s["reflection_log"][0]["attempt_no"] == 1
    assert s["reflection_count"] == 1


def test_reflection_log_append_reducer():
    existing = [{"attempt_no": 1, "new_hypothesis": "h1"}]
    merged = append_records(existing, [{"attempt_no": 2, "new_hypothesis": "h2"}])
    assert len(merged) == 2
    assert merged[1]["attempt_no"] == 2
