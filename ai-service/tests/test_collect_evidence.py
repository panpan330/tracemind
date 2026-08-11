"""collect_evidence 混合循环单测:mock LLM 选择器与工具执行。"""
from app.agent.nodes import collect_evidence


class StubLLM:
    """返回固定 tool_calls 队列;空 → 无调用。"""
    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.select_tool_calls = 0

    def select_tool(self, state, prompt, eligible_tools):
        self.select_tool_calls += 1
        return self.rounds.pop(0) if self.rounds else []


class StubTools:
    def __init__(self, results):
        self.results = list(results)
        self.executed = []

    def __call__(self, state, name, args):
        self.executed.append(name)
        return self.results.pop(0) if self.results else {"ok": False, "error": "no fixture"}


def base_state(**overrides):
    state = {
        "incident_id": 1, "run_id": 1, "service_ref": "inventory-service",
        "description": "x", "status": "investigating",
        "hypotheses": [], "evidence": [], "evidence_gate": {},
        "investigation_round": 0, "tool_call_count": 0,
        "decision_attempt_count": 0, "tool_execution_count": 0,
        "consecutive_invalid_count": 0, "consecutive_no_progress_count": 0,
    }
    state.update(overrides)
    return state


def test_evidence_full_skips_loop():
    state = base_state(evidence_gate={"E1": True, "E2": True, "E3": True,
                                      "E4": True, "E5": True})
    out = collect_evidence(state, llm=StubLLM([]), tools=StubTools([]))
    assert "status" not in out or out.get("status") != "needs_human"


def test_invalid_tool_increments_invalid_and_not_execution():
    state = base_state()
    llm = StubLLM([[{"id": "c1", "name": "drop_table", "arguments": {}}]])
    tools = StubTools([])
    out = collect_evidence(state, llm=llm, tools=tools)
    assert out["consecutive_invalid_count"] == 1
    assert out["tool_execution_count"] == 0
    assert tools.executed == []


def test_valid_execution_increments_execution():
    state = base_state(evidence_gate={"E2": True, "E3": True, "E4": True, "E5": True})
    llm = StubLLM([[{"id": "c1", "name": "get_service_metrics", "arguments": {}}]])
    tools = StubTools([{"ok": True, "evidence": [{"key": "e1", "source": "get_service_metrics",
                                                  "content": {"p95Ms": 200}, "passed": True}]}])
    out = collect_evidence(state, llm=llm, tools=tools)
    assert out["tool_execution_count"] == 1
    assert tools.executed == ["get_service_metrics"]


def test_budget_exhausted_sets_needs_human():
    state = base_state(decision_attempt_count=10)
    out = collect_evidence(state, llm=StubLLM([[]]), tools=StubTools([]))
    assert out.get("status") == "needs_human"


def test_noop_two_rounds_sets_needs_human():
    state = base_state(consecutive_no_progress_count=1)
    out = collect_evidence(state, llm=StubLLM([[]]), tools=StubTools([]))
    assert out.get("status") == "needs_human"
