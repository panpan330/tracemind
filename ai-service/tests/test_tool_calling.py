"""Tool Calling 核心单测:eligible/校验/参数解析/去重。"""
import pytest

from app.agent.tool_calling import (MAX_CONSECUTIVE_INVALID, DuplicateGuard,
                                    compute_eligible_tools, resolve_arguments,
                                    validate_tool_call)
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
    assert tools == {"get_service_metrics", "list_expensive_query_digests", "get_index_info"}


def test_eligible_with_trace_id_adds_trace():
    state = base_state(evidence_gate={"e1": True})
    state["evidence"] = [{"key": "e1", "content": {"representativeSlowTraceId": "t1"}}]
    tools = compute_eligible_tools(state)
    assert "get_trace" in tools


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


def test_resolve_trace_from_evidence():
    state = base_state()
    state["evidence"] = [{"key": "e1", "content": {"representativeSlowTraceId": "t1"}}]
    args = resolve_arguments("get_trace", {"trace_ref": "representative_slow_trace"}, state)
    assert args["trace_id"] == "t1"


def test_resolve_trace_without_evidence_raises():
    with pytest.raises(Exception):
        resolve_arguments("get_trace", {"trace_ref": "representative_slow_trace"}, base_state())


def test_duplicate_guard_blocks_same_key():
    guard = DuplicateGuard()
    dup1, key = guard.check("get_index_info", {"table_ref": "inventory"})
    dup2, _ = guard.check("get_index_info", {"table_ref": "inventory"})
    assert not dup1 and key  # 第一次通过
    assert dup2              # 第二次重复


def test_max_consecutive_invalid_constant():
    assert MAX_CONSECUTIVE_INVALID == 2
