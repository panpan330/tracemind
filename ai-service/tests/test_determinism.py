"""确定性降级组件单测:不触网。"""
from app.agent.determinism import (DeterministicEvidencePlanner,
                                   TemplateHypothesisGenerator,
                                   TemplatePostmortemRenderer)


def base_state(**overrides):
    state = {
        "incident_id": 1, "run_id": 1, "service_ref": "inventory-service",
        "description": "库存查询变慢", "status": "investigating",
        "evidence": [], "hypotheses": [], "evidence_gate": {},
        "fix_execution": {"status": "succeeded"}, "recovery": {"status": "recovered"},
    }
    state.update(overrides)
    return state


def test_template_hypothesis_returns_builtin():
    hyps = TemplateHypothesisGenerator().generate(base_state())
    assert hyps[0]["description"]
    assert hyps[0]["status"] == "proposed"


def test_planner_picks_first_missing_evidence():
    # E1 缺失 → 只选 get_service_metrics(即使 eligible 有多个)
    planner = DeterministicEvidencePlanner()
    state = base_state(evidence_gate={"e2": True, "e3": True, "e4": True, "e5": True})
    calls = planner.choose(state, eligible_tools={"get_service_metrics", "get_index_info"})
    assert calls[0]["name"] == "get_service_metrics"


def test_planner_respects_eligible():
    state = base_state()
    calls = DeterministicEvidencePlanner().choose(state, eligible_tools=set())
    assert calls == []


def test_planner_e2_without_trace_id_falls_back_to_metrics():
    # E2 缺失但无 trace_id → 回退 metrics(拿代表性 trace)
    state = base_state(evidence_gate={"e1": True, "e2": False, "e3": True, "e4": True, "e5": True})
    calls = DeterministicEvidencePlanner().choose(state, eligible_tools={"get_trace", "get_service_metrics"})
    assert calls[0]["name"] == "get_service_metrics"


def test_planner_e2_with_trace_id_picks_trace():
    # E2 缺失且有合法 trace_id → 选 get_trace
    state = base_state(trigger_trace_id="t1",
                       evidence_gate={"e1": True, "e2": False, "e3": True, "e4": True, "e5": True})
    calls = DeterministicEvidencePlanner().choose(state, eligible_tools={"get_trace", "get_service_metrics"})
    assert calls[0]["name"] == "get_trace"
    assert calls[0]["arguments"]["trace_id"] == "t1"


def test_template_report_uses_facts_only():
    report = TemplatePostmortemRenderer().render(base_state())
    assert "复盘" in report["content"] or "根因" in report["content"]
    assert report["root_cause_summary"]


# ---- V1.3:锁证据链(L1→L2)----

def test_planner_collects_lock_chain():
    planner = DeterministicEvidencePlanner()
    # 索引链已齐(证据不足时才进锁链补采)
    state = {"evidence_gate": {"E1": True, "E2": True, "E3": True, "E4": True, "E5": True},
             "evidence": [],
             "service_ref": "inventory-service",
             "policy": {"scn001": "unknown", "scn002": "unknown"}}
    eligible = {"get_lock_waiters"}
    calls = planner.choose(state, eligible)
    assert calls and calls[0]["name"] == "get_lock_waiters"  # 锁证据缺失 → 补采


def test_planner_transaction_details_after_lock():
    planner = DeterministicEvidencePlanner()
    state = {"evidence_gate": {"E1": True, "E2": True, "E3": True, "E4": True, "E5": True},
             "evidence": [{"key": "l1", "content": {"waits": [{"blocker_ref": "blk_1",
                       "object_schema": "tracemind_business", "object_table": "inventory",
                       "waiting_query_ref": "INVENTORY_RESERVATION"}]}, "passed": True}]}
    eligible = {"get_transaction_details"}
    calls = planner.choose(state, eligible)
    assert calls and calls[0]["name"] == "get_transaction_details"
    assert calls[0]["arguments"]["transaction_ref"] == "blk_1"
