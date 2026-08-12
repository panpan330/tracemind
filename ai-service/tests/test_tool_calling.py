"""Tool Calling 核心单测:eligible/校验/参数解析/去重。"""
import pytest

from app.agent.tool_calling import (MAX_CONSECUTIVE_INVALID, DuplicateGuard,
                                    compute_eligible_tools, resolve_arguments,
                                    validate_tool_call)
from app.agent import tool_calling
from app.agent.tool_schemas import ALLOWED_TOOLS, TOOL_SCHEMAS


def base_state(**overrides):
    state = {"incident_id": 1, "run_id": 1, "service_ref": "inventory-service",
             "evidence": [], "evidence_gate": {}, "investigation_round": 0}
    state.update(overrides)
    return state


def test_schemas_exclude_write_tools():
    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert "get_service_metrics" in names
    assert "execute_fix" not in names
    assert "verify_recovery" not in names
    assert ALLOWED_TOOLS == {"get_service_metrics", "get_trace",
                             "list_expensive_query_digests", "get_query_plan",
                             "get_index_info", "get_lock_waiters",
                             "get_transaction_details"}


def test_eligible_all_missing():
    tools = compute_eligible_tools(base_state())
    # V1.3:锁证据未采时 get_lock_waiters 也 eligible;transaction_details 需 blocker_ref
    assert tools == {"get_service_metrics", "list_expensive_query_digests",
                     "get_index_info", "get_lock_waiters"}



def test_eligible_with_query_ref_adds_plan():
    state = base_state(evidence_gate={"e1": True, "e3": True})
    state["evidence"] = [{"key": "e3", "content": {"query_ref": "INVENTORY_LOOKUP"}}]
    tools = compute_eligible_tools(state)
    assert "get_query_plan" in tools


def test_validate_rejects_unknown_tool():
    assert validate_tool_call("drop_table", {}, {"get_service_metrics"}) is not None


def test_validate_rejects_not_eligible():
    assert validate_tool_call("get_index_info", {"table_ref": "inventory"},
                              {"get_service_metrics"}) is not None


def test_validate_rejects_bad_enum():
    err = validate_tool_call("get_index_info", {"table_ref": "users"},
                             {"get_index_info"})
    assert err is not None


def test_validate_accepts_valid():
    assert validate_tool_call("get_index_info", {"table_ref": "inventory"},
                              {"get_index_info"}) is None


def test_resolve_metrics_service_from_state():
    args = resolve_arguments("get_service_metrics", {"service_ref": "x"}, base_state())
    assert args["service_ref"] == "inventory-service"




def test_duplicate_guard_blocks_same_key():
    guard = DuplicateGuard()
    dup1, key = guard.check("get_index_info", {"table_ref": "inventory"})
    dup2, _ = guard.check("get_index_info", {"table_ref": "inventory"})
    assert not dup1 and key  # 第一次通过
    assert dup2              # 第二次重复


def test_max_consecutive_invalid_constant():
    assert MAX_CONSECUTIVE_INVALID == 2


# ---- V1.3:预算与锁工具资格 ----

def test_budget_v13():
    assert tool_calling.MAX_DECISION_ATTEMPTS == 14
    assert tool_calling.MAX_TOOL_EXECUTIONS == 10
    assert tool_calling.MAX_LOCK_EVIDENCE_REFRESH == 1


def test_eligible_includes_lock_tools_when_lock_facts_unknown():
    state = {"evidence_gate": {}, "evidence": []}
    eligible = tool_calling.compute_eligible_tools(state)
    assert "get_lock_waiters" in eligible
    assert "get_transaction_details" not in eligible  # 需先有 blocker_ref


def test_transaction_details_eligible_after_lock_waiters():
    state = {"evidence_gate": {},
             "evidence": [{"key": "l1", "content": {"waits": [{"blocker_ref": "blk_1",
                       "object_schema": "tracemind_business", "object_table": "inventory",
                       "waiting_query_ref": "INVENTORY_RESERVATION"}]}, "passed": True}]}
    eligible = tool_calling.compute_eligible_tools(state)
    assert "get_transaction_details" in eligible


def test_resolve_lock_tools_parameters():
    state = {"service_ref": "inventory-service",
             "evidence": [{"key": "l1", "content": {"waits": [{"blocker_ref": "blk_1",
                       "object_schema": "tracemind_business", "object_table": "inventory",
                       "waiting_query_ref": "INVENTORY_RESERVATION"}]}, "passed": True}]}
    args = tool_calling.resolve_arguments("get_lock_waiters", {}, state)
    assert args["scope_ref"] == "INVENTORY_RESERVATION"
    args2 = tool_calling.resolve_arguments("get_transaction_details",
                                           {"transaction_ref": "OBSERVED_BLOCKER"}, state)
    # 受控引用:程序从有效 l1 证据解析 blocker_ref(非 LLM 编造)
    assert args2["transaction_ref"] == "blk_1"


def test_get_trace_eligible_with_metrics_window():
    state = {"incident_id": 1,
             "affected_service_ref": "inventory-service",
             "affected_operation_ref": "INVENTORY_LOOKUP",
             "evidence_gate": {"E1": True},
             "evidence": [{"key": "e1", "content": {"windowStart": "a", "windowEnd": "b"}}]}
    eligible = tool_calling.compute_eligible_tools(state)
    assert "get_trace" in eligible


def test_get_trace_not_eligible_without_window():
    state = {"incident_id": 1, "evidence_gate": {}, "evidence": []}
    eligible = tool_calling.compute_eligible_tools(state)
    assert "get_trace" not in eligible


def test_resolve_get_trace_trace_ref():
    state = {"incident_id": 1, "affected_service_ref": "inventory-service",
             "affected_operation_ref": "INVENTORY_RESERVATION",
             "observed_at": "2026-08-12T00:00:00Z"}
    out = tool_calling.resolve_arguments("get_trace", {"trace_ref": "REPRESENTATIVE_SLOW_TRACE"}, state)
    assert out["service_ref"] == "inventory-service"
    assert out["operation_ref"] == "INVENTORY_RESERVATION"
    assert out["strategy"] == "SLOWEST"


def test_resolve_get_trace_trace_id_priority():
    state = {"incident_id": 1, "affected_service_ref": "inventory-service",
             "affected_operation_ref": "INVENTORY_RESERVATION"}
    out = tool_calling.resolve_arguments("get_trace", {"trace_id": "abc123"}, state)
    assert out["trace_id"] == "abc123"
