"""V2.0-A 行为特征测试:钉住 SCN-001/SCN-002 现有诊断行为(基线可信化前后不变)。

特征维度(升级方案 V2.0 实施内容第 4 条):
- 相同输入 → 根因代码不变;
- 工具白名单不变;
- 证据不足 / 证据冲突 / 预算耗尽 → 仍转人工;
- 审批、执行、验证字段保持兼容。
"""
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agent import policies
from app.agent.graph import build_graph
from app.agent.nodes import collect_evidence, diagnose
from app.agent.tool_calling import compute_eligible_tools

# ---------- SCN-001 fixture(缺索引;与 verify-m5 真实链路同构) ----------

INDEX_MCP = {
    "get_service_metrics": {"success": True, "data": {"p95Ms": 150, "sourceBackend": "fixture"}},
    "get_trace": {"success": True, "data": {"sourceBackend": "fixture",
                 "inventoryServerDurationMs": 90, "targetDbDurationMs": 85,
                 "dbDominanceRatio": 0.9, "targetDbSpanId": "s3",
                 "normalizationRuleVersion": "TRACE_NORMALIZER_V1"}},
    "list_expensive_query_digests": {"success": True,
                                     "data": [{"rows_examined_delta": 100_000, "count_delta": 10}]},
    "get_query_plan": {"success": True,
                       "data": {"explain": {"query_block": {"table": {"access_type": "ALL"}}}}},
    "get_index_info": {"success": True, "data": {"indexes": [{"index_name": "PRIMARY"}]}},
    "get_lock_waiters": {"success": True, "data": {"waits": []}},
    "get_transaction_details": {"success": False, "data": None, "error_message": "TRX_NOT_FOUND"},
    "verify_recovery": {"success": True, "data": {"status": "recovered", "latency_p95_after": 3}},
}

# ---------- SCN-002 fixture(长事务持锁;与 verify-m13 真实链路同构) ----------

_LOCK_WAIT = {
    "blocker_ref": "blk_1", "blocking_transaction_id": 88, "blocking_processlist_id": 88,
    "blocking_lock_ref": "blk_1", "requesting_transaction_id": 100,
    "requesting_processlist_id": 101, "index_name": "PRIMARY",
    "object_schema": "tracemind_business", "object_table": "inventory",
    "waiting_query_ref": "INVENTORY_RESERVATION", "wait_duration_ms": 3500,
}
LOCK_MCP = {
    "get_service_metrics": {"success": True, "data": {"p95Ms": 150, "sourceBackend": "fixture"}},
    "get_trace": {"success": True, "data": {"sourceBackend": "fixture",
                 "inventoryServerDurationMs": 900, "targetDbDurationMs": 820,
                 "dbDominanceRatio": 0.9, "targetDbSpanId": "s3",
                 "normalizationRuleVersion": "TRACE_NORMALIZER_V1"}},
    "list_expensive_query_digests": {"success": True, "data": []},
    "get_query_plan": {"success": True,
                       "data": {"explain": {"query_block": {"table": {"access_type": "ref"}}}}},
    "get_index_info": {"success": True,
                       "data": {"indexes": [{"index_name": "PRIMARY"},
                                            {"index_name": "idx_sku_warehouse"}]}},
    "get_lock_waiters": {"success": True, "data": {"waits": [_LOCK_WAIT]}},
    "get_transaction_details": {"success": True,
                                "data": {"transaction_id": 88, "processlist_id": 88,
                                         "age_ms": 12000}},
}


class FakeApproval:
    id = 5
    status = "pending"


def _patch_mcp(monkeypatch, fixtures):
    class FakeMCP:
        def call_tool(self, name, incident_id, agent_run_id, **business):
            return fixtures[name]

    monkeypatch.setattr("app.agent.nodes.get_mcp_client", lambda: FakeMCP())
    # verify_recovery 属确定性安全控制节点(不纳入 MCP),经 nodes.execute_tool 直调
    monkeypatch.setattr("app.agent.nodes.execute_tool",
                        lambda tool, incident_id=None, agent_run_id=None, **kw:
                        fixtures.get(tool, {"success": False, "data": None}))


def _patch_repos(monkeypatch, executed=None):
    """落库类依赖替换(不产生真实 proposal/approval 行)。"""
    def fake_execute_fix(incident_id, fix_proposal_id, approval_id):
        if executed is not None:
            executed.append(("fix_service", incident_id))
        return {"status": "succeeded", "fix_execution_id": 9}

    monkeypatch.setattr("app.agent.nodes.approval_repo.create_approval",
                        lambda **kw: FakeApproval())
    monkeypatch.setattr("app.agent.nodes.proposal_repo.create_proposal",
                        lambda **kw: type("P", (), {"id": 1})())
    monkeypatch.setattr("app.agent.nodes.hypothesis_repo.upsert_hypothesis",
                        lambda *a, **kw: {"id": 1})
    monkeypatch.setattr("app.agent.nodes.evidence_repo.upsert_evidence",
                        lambda *a, **kw: {"id": 1})
    monkeypatch.setattr("app.agent.nodes.fix_service.execute_fix", fake_execute_fix)
    monkeypatch.setattr("app.agent.nodes.postmortem_repo.create_postmortem",
                        lambda incident_id, content: {"id": 1})


def _base_state(**overrides):
    state = {"incident_id": 1, "service_ref": "inventory-service", "severity": "high",
             "max_investigation_rounds": 1, "max_tool_calls": 5}
    state.update(overrides)
    return state


# ---------- 特征 1:相同输入 → 相同根因代码(SCN-001) ----------

def test_scn001_root_cause_and_action_stable(monkeypatch):
    _patch_mcp(monkeypatch, INDEX_MCP)
    _patch_repos(monkeypatch)
    graph = build_graph(checkpointer=InMemorySaver())
    first = graph.invoke(_base_state(), config={"thread_id": "char-scn001-1",
                                                "recursion_limit": 100})
    # 根因与提案(fail closed 重构后必须逐项不变)
    assert first["root_cause_code"] == "MISSING_INVENTORY_INDEX"
    assert first["fix_proposal"]["action_type"] == "CREATE_INVENTORY_INDEX"
    assert first["fix_proposal"]["risk_level"] == "medium"
    assert first["fix_proposal"]["parameters"]["index_name"] == "idx_sku_warehouse"
    assert first["fix_proposal"]["parameters"]["columns"] == ["sku_id", "warehouse_id"]
    # 审批字段兼容
    assert first["status"] == "awaiting_approval"
    assert first["approval"]["status"] == "pending"
    assert first["approval"]["approval_id"] == 5
    # 证据链形状不变(E1~E5 齐 + L1 否定)
    ev = {e["id"]: e["passed"] for e in first["evidence"]}
    assert {k: ev[k] for k in ("E1", "E2", "E3", "E4", "E5")} == {
        "E1": True, "E2": True, "E3": True, "E4": True, "E5": True}
    assert ev["L1"] is False

    second = graph.invoke(Command(resume={"decision": "approved"}),
                          config={"thread_id": "char-scn001-1", "recursion_limit": 100})
    assert second["status"] == "recovered"
    assert second["fix_execution"]["status"] == "succeeded"
    assert second["fix_execution"]["fix_execution_id"] == 9
    assert second["recovery"]["status"] == "recovered"
    assert second["report"]["content"]


# ---------- 特征 1b:相同输入 → 相同根因代码(SCN-002) ----------

def test_scn002_root_cause_and_action_stable(monkeypatch):
    executed = []
    _patch_mcp(monkeypatch, LOCK_MCP)
    _patch_repos(monkeypatch, executed)

    def fake_terminator(proposal, approval):
        executed.append(("terminator", proposal.get("parameters", {}).get("processlist_id")))
        return {"execution_result": "executed", "kill_attempted": True,
                "actual_processlist_id": 88}

    monkeypatch.setattr("app.services.session_terminator.execute", fake_terminator)
    # verify 阶段:目标锁等待已消失 + 三批探测成功
    monkeypatch.setattr("app.tools.lock_queries.get_lock_waiters",
                        lambda *a, **kw: {"data": {"waits": []}})
    monkeypatch.setattr("app.agent.nodes._run_probe_batches",
                        lambda state, batches=3: [{"success": True}] * batches)

    graph = build_graph(checkpointer=InMemorySaver())
    first = graph.invoke(_base_state(affected_operation_ref="INVENTORY_RESERVATION"),
                         config={"thread_id": "char-scn002-1", "recursion_limit": 100})
    assert first["root_cause_code"] == policies.ROOT_CAUSE_LOCK
    assert first["fix_proposal"]["action_type"] == "TERMINATE_BLOCKING_SESSION"
    assert first["fix_proposal"]["risk_level"] == "high"
    # 参数来自证据(根阻塞者 88),非 LLM 自由填写
    assert first["fix_proposal"]["parameters"]["blocking_processlist_id"] == 88
    assert first["fix_proposal"]["blocking_relation_hash"]
    assert first["status"] == "awaiting_approval"
    # 证据链形状:E3/E4/E5 确定性否定 + L1/L2 正向
    ev = {e["id"]: e["passed"] for e in first["evidence"]}
    assert ev["E3"] is False and ev["E4"] is False and ev["E5"] is False
    assert ev["L1"] is True and ev["L2"] is True

    second = graph.invoke(Command(resume={"decision": "approved"}),
                          config={"thread_id": "char-scn002-1", "recursion_limit": 100})
    assert second["status"] == "recovered"
    assert second["fix_execution"]["status"] == "succeeded"
    assert second["fix_execution"]["execution_result"] == "executed"
    assert second["recovery"]["status"] == "recovered"
    assert ("terminator", 88) in executed


# ---------- 特征 2:工具白名单不变 ----------

def _elig(evidence, **extra):
    state = {"evidence_gate": {}, "evidence": evidence,
             "service_ref": "inventory-service"}
    state.update(extra)
    return sorted(compute_eligible_tools(state))


def test_tool_whitelist_initial_stable():
    assert _elig([]) == ["get_index_info", "get_lock_waiters", "get_service_metrics",
                         "list_expensive_query_digests"]


def test_tool_whitelist_after_e1_stable():
    ev = [{"key": "e1", "content": {"p95Ms": 150, "sourceBackend": "fixture"}}]
    assert _elig(ev) == ["get_index_info", "get_lock_waiters", "get_trace",
                         "list_expensive_query_digests"]


def test_tool_whitelist_after_e1_e3_stable():
    ev = [{"key": "e1", "content": {"p95Ms": 150, "sourceBackend": "fixture"}},
          {"key": "e3", "content": {"top": {"rows_examined_delta": 200000},
                                    "query_ref": "INVENTORY_LOOKUP"}}]
    assert _elig(ev) == ["get_index_info", "get_lock_waiters", "get_query_plan", "get_trace"]


def test_tool_whitelist_lock_observed_unlocks_transaction_details():
    ev = [{"key": "l1", "content": {"waits": [dict(_LOCK_WAIT)]}},
          {"key": "e1", "content": {"p95Ms": 150, "sourceBackend": "fixture"}}]
    assert _elig(ev) == ["get_index_info", "get_trace", "get_transaction_details",
                         "list_expensive_query_digests"]


# ---------- 特征 3:证据冲突 / 预算耗尽 → 转人工 ----------

def test_both_policies_confirmed_routes_needs_human(monkeypatch):
    monkeypatch.setattr("app.agent.nodes.incident_repo.update_state",
                        lambda *a, **kw: None)
    state = {"incident_id": 1, "status": "investigating",
             "policy": {"scn001": "confirmed", "scn002": "confirmed"}, "facts": {}}
    out = diagnose(state)
    assert out["status"] == "needs_human"
    assert out["termination_reason"] == "multiple_confirmed_causes"


def test_decision_budget_exhausted_needs_human():
    state = {"incident_id": 1, "run_id": 1, "service_ref": "inventory-service",
             "status": "investigating", "hypotheses": [], "evidence": [],
             "evidence_gate": {}, "decision_attempt_count": 14,
             "tool_execution_count": 0, "consecutive_invalid_count": 0,
             "consecutive_no_progress_count": 0}
    out = collect_evidence(state)
    assert out["status"] == "needs_human"
    assert out["termination_reason"] == "decision_budget_exhausted"


def test_execution_budget_exhausted_needs_human():
    state = {"incident_id": 1, "run_id": 1, "service_ref": "inventory-service",
             "status": "investigating", "hypotheses": [], "evidence": [],
             "evidence_gate": {}, "decision_attempt_count": 0,
             "tool_execution_count": 20, "consecutive_invalid_count": 0,
             "consecutive_no_progress_count": 0}

    class OneCall:
        def select_tool(self, state, prompt, eligible):
            return [{"id": "c1", "name": "get_index_info",
                     "arguments": {"table_ref": "inventory"}}]

    out = collect_evidence(state, llm=OneCall())
    assert out["status"] == "needs_human"
    assert out["termination_reason"] == "execution_budget_exhausted"


def test_no_progress_budget_needs_human():
    state = {"incident_id": 1, "run_id": 1, "service_ref": "inventory-service",
             "status": "investigating", "hypotheses": [], "evidence": [],
             "evidence_gate": {}, "decision_attempt_count": 0,
             "tool_execution_count": 0, "consecutive_invalid_count": 0,
             "consecutive_no_progress_count": 3}

    class OneCall:
        def select_tool(self, state, prompt, eligible):
            return [{"id": "c1", "name": "get_index_info",
                     "arguments": {"table_ref": "inventory"}}]

    out = collect_evidence(state, llm=OneCall(), tools=lambda *a, **kw: {"ok": True})
    assert out["status"] == "needs_human"
    assert out["termination_reason"] == "no_progress"
