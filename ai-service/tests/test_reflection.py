"""V1.10 反思循环:state 字段、reflect 节点、graph 条件边。"""
from app.agent import nodes
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


class _FakeReflectLLM:
    """带 reflect 高层方法的假 LLM(与 OpenAICompatibleLLM/FakeLLM 同接口)。"""

    def reflect(self, state):
        return {
            "root_cause_revisit": "根因判断正确,但证据链缺关键指标",
            "evidence_gap": "缺少 p95 耗时对比数据",
            "new_hypothesis": "疑为索引缺失叠加连接池耗尽",
            "adjust_strategy": "补查索引状态与连接池指标",
        }


def test_reflect_outputs_structured_fields(monkeypatch):
    state = {
        "incident_id": 1, "run_id": 1, "description": "库存查询慢",
        "status": "needs_human", "root_cause_code": "INDEX_MISSING",
        "reflection_count": 0, "reflection_log": [],
        "recovery": {"status": "needs_human"},
        "evidence": [{"id": "e1", "passed": False}],
        "fix_execution": {"status": "failed"},
    }
    monkeypatch.setattr(nodes, "get_llm", lambda: _FakeReflectLLM())
    out = nodes.reflect(state)
    assert out["reflection_count"] == 1
    assert out["reflection_log"][0]["attempt_no"] == 1
    assert out["reflection_log"][0]["new_hypothesis"] == "疑为索引缺失叠加连接池耗尽"
    assert out["reflection_log"][0]["strategy_change"] == "补查索引状态与连接池指标"


def test_reflect_llm_unavailable_degrades(monkeypatch):
    state = {
        "incident_id": 1, "run_id": 1, "description": "d", "status": "needs_human",
        "root_cause_code": "X", "reflection_count": 0, "reflection_log": [],
        "recovery": {"status": "needs_human"}, "evidence": [], "fix_execution": {},
    }

    class _BrokenLLM:
        def reflect(self, state):
            raise RuntimeError("LLM down")

    monkeypatch.setattr(nodes, "get_llm", lambda: _BrokenLLM())
    out = nodes.reflect(state)
    assert out["status"] == "needs_human"
    assert out["termination_reason"] == "reflection_llm_unavailable"
