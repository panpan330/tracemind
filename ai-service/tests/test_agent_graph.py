from app.agent.graph import build_graph
from app.agent.nodes import report


def test_graph_compiles():
    graph = build_graph()
    assert graph is not None


def test_full_evidence_path_reaches_awaiting_approval(monkeypatch):
    class FakeProposal:
        id = 7

    def fake_execute(tool, incident_id=None, **kwargs):
        if tool == "get_service_metrics":
            return {"success": True,
                    "data": {"p95Ms": 150, "representativeSlowTraceId": "t1"}}
        if tool == "get_trace":
            return {"success": True, "data": {"inventory_service": [
                {"stage": "database", "durationMs": 90},
                {"stage": "total", "durationMs": 100}]}}
        if tool == "list_expensive_query_digests":
            return {"success": True,
                    "data": [{"rows_examined_delta": 100_000, "count_delta": 10}]}
        if tool == "get_query_plan":
            return {"success": True,
                    "data": {"explain": {"query_block": {"table": {"access_type": "ALL"}}}}}
        if tool == "get_index_info":
            return {"success": True, "data": {"indexes": [{"index_name": "PRIMARY"}]}}
        if tool == "get_lock_waiters":
            # 有效否定结论:无目标锁等待(成功返回空列表)
            return {"success": True, "data": {"observed_at": "2026-08-11T00:00:00Z",
                "snapshot_expires_at": "2026-08-11T00:00:20Z", "waits": []}}
        return {"success": False, "data": None}

    def fake_create_proposal(**kwargs):
        return FakeProposal()

    class FakeMCP:
        def call_tool(self, name, incident_id, agent_run_id, **business):
            return fake_execute(name, incident_id=incident_id, **business)

    monkeypatch.setattr("app.agent.nodes.get_mcp_client", lambda: FakeMCP())
    monkeypatch.setattr("app.agent.nodes.proposal_repo.create_proposal", fake_create_proposal)
    monkeypatch.setattr("app.agent.nodes.hypothesis_repo.upsert_hypothesis",
                        lambda *a, **kw: {"id": 1})
    monkeypatch.setattr("app.agent.nodes.evidence_repo.upsert_evidence",
                        lambda *a, **kw: {"id": 1})
    graph = build_graph()
    state = {"incident_id": 1, "service_ref": "inventory-service", "severity": "high"}
    result = graph.invoke(state)
    assert result["confirmed_hypothesis_id"] == "h1"
    gate = result["evidence_gate"]
    assert all(gate[k] for k in ("E1", "E2", "E3", "E4", "E5"))
    assert result["status"] == "awaiting_approval"
    assert result["fix_proposal"]["fix_proposal_id"] == 7
    assert result["fix_proposal"]["action_type"] == "CREATE_INVENTORY_INDEX"


def test_incomplete_evidence_keeps_collecting(monkeypatch):
    def fake_execute(tool, incident_id=None, **kwargs):
        # 所有证据都不满足:指标正常、无慢 trace、无 digest 增量、非全表扫描、索引存在
        if tool == "get_service_metrics":
            return {"success": True,
                    "data": {"p95Ms": 2, "representativeSlowTraceId": None}}
        if tool == "get_trace":
            return {"success": False, "data": None}
        if tool == "list_expensive_query_digests":
            return {"success": True, "data": []}
        if tool == "get_query_plan":
            return {"success": True,
                    "data": {"explain": {"query_block": {"table": {"access_type": "ref"}}}}}
        if tool == "get_index_info":
            return {"success": True,
                    "data": {"indexes": [{"index_name": "idx_sku_warehouse"}]}}
        return {"success": False, "data": None}

    class FakeMCP:
        def call_tool(self, name, incident_id, agent_run_id, **business):
            return fake_execute(name, incident_id=incident_id, **business)

    monkeypatch.setattr("app.agent.nodes.get_mcp_client", lambda: FakeMCP())
    monkeypatch.setattr("app.agent.nodes.hypothesis_repo.upsert_hypothesis",
                        lambda *a, **kw: {"id": 1})
    monkeypatch.setattr("app.agent.nodes.evidence_repo.upsert_evidence",
                        lambda *a, **kw: {"id": 1})
    graph = build_graph()
    state = {"incident_id": 2, "service_ref": "inventory-service",
             "severity": "high", "max_investigation_rounds": 1, "max_tool_calls": 5}
    result = graph.invoke(state)
    assert result["status"] == "needs_human"
    # V1.1 预算语义:无故障场景(E1 正常)重复调用同一工具被去重拦截后转人工
    assert result["termination_reason"] == "duplicate_tool_call"
    assert result.get("confirmed_hypothesis_id") is None


def test_report_node_writes_postmortem(monkeypatch):
    class FakePostmortem:
        id = 3

    def fake_create_postmortem(incident_id, content):
        assert incident_id == 1
        return FakePostmortem()

    monkeypatch.setattr("app.agent.nodes.postmortem_repo.create_postmortem",
                        fake_create_postmortem)
    state = {
        "incident_id": 1,
        "status": "recovered",
        "confirmed_hypothesis_id": "h1",
        "evidence": [{"id": "E1", "source": "get_service_metrics",
                      "content": {"p95Ms": 120}, "passed": True}],
        "recovery": {"status": "recovered", "latency_p95_after": 3},
    }
    result = report(state)
    assert result["status"] == "recovered"
    assert "根因" in result["report"]["content"]


def test_lock_wait_graph_reaches_confirmed(monkeypatch):
    """SCN-002 锁证据齐全 → 根因 LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION。"""
    from app.agent.graph import build_graph

    calls = []

    def fake_execute(tool, incident_id=None, **kwargs):
        calls.append(tool)
        if tool == "get_service_metrics":
            return {"success": True, "data": {"p95Ms": 117, "representativeSlowTraceId": "t1"}}
        if tool == "get_trace":
            return {"success": True, "data": {"inventory_service": [
                {"stage": "database", "durationMs": 110}, {"stage": "total", "durationMs": 120}]}}
        if tool == "list_expensive_query_digests":
            return {"success": True, "data": [{"query_ref": "INVENTORY_LOOKUP",
                                               "rows_examined_delta": 500}]}
        if tool == "get_query_plan":
            return {"success": True, "data": {"explain": {
                "query_block": {"table": {"access_type": "ref"}}}}}
        if tool == "get_index_info":
            return {"success": True, "data": {"indexes": [
                {"index_name": "idx_sku_warehouse"}]}}
        if tool == "get_lock_waiters":
            return {"success": True, "data": {"observed_at": "2026-08-11T00:00:00Z",
                "snapshot_expires_at": "2026-08-11T00:00:20Z",
                "waits": [{"blocker_ref": "blk_1", "blocking_transaction_id": 88,
                           "blocking_processlist_id": 88,
                           "object_schema": "tracemind_business", "object_table": "inventory",
                           "index_name": "idx_sku_warehouse", "lock_type": "RECORD",
                           "lock_mode": "X", "wait_duration_ms": 5200,
                           "waiting_query_ref": "INVENTORY_RESERVATION"}]}}
        if tool == "get_transaction_details":
            return {"success": True, "data": {"transaction_id": 88, "processlist_id": 88,
                "account": "app_business", "age_ms": 12000,
                "statement_digest": "UPDATE inventory SET quantity=...",
                "locked_objects": [{"schema": "tracemind_business", "table": "inventory",
                                    "lock_ref": "lr2"}],
                "observed_at": "2026-08-11T00:00:00Z",
                "snapshot_expires_at": "2026-08-11T00:00:20Z"}}
        return {"success": False, "data": None}

    class FakeMCP:
        def call_tool(self, name, incident_id, agent_run_id, **business):
            return fake_execute(name, incident_id=incident_id, **business)

    monkeypatch.setattr("app.agent.nodes.get_mcp_client", lambda: FakeMCP())
    monkeypatch.setattr("app.agent.nodes.proposal_repo.create_proposal",
                        lambda **kw: type("P", (), {"id": 7})())
    monkeypatch.setattr("app.agent.nodes.hypothesis_repo.upsert_hypothesis",
                        lambda *a, **kw: {"id": 1})
    monkeypatch.setattr("app.agent.nodes.evidence_repo.upsert_evidence",
                        lambda *a, **kw: {"id": 1})

    state = {"incident_id": 9, "run_id": 9, "service_ref": "inventory-service",
             "severity": "high", "max_investigation_rounds": 5, "max_tool_calls": 25,
             "policy": {}, "facts": {}}
    graph = build_graph()
    result = graph.invoke(state)
    assert result["root_cause_code"] == (
        "LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION")
    assert result.get("confirmed_hypothesis_id") == "h1"


def test_lock_recovery_checks_target_scope(monkeypatch):
    """锁根因恢复:目标锁关系消失 → 三批探测通过 → recovered(不要求全库无锁)。"""
    from app.agent import nodes

    class FakeLockQueries:
        def get_lock_waiters(self, *a, **kw):
            return {"ok": True, "data": {"waits": []}}  # 目标锁关系已消失

    monkeypatch.setattr("app.tools.lock_queries.get_lock_waiters",
                        FakeLockQueries().get_lock_waiters)
    monkeypatch.setattr(nodes, "_run_probe_batches",
                        lambda state, batches=3: [{"success": True}] * batches)

    state = {"incident_id": 9, "run_id": 9, "status": "executing",
             "root_cause_code": "LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION",
             "fix_execution": {"status": "succeeded"}}
    out = nodes.verify_recovery_node(state)
    assert out.get("recovery", {}).get("status") == "recovered"
    assert state["status"] == "recovered"


def test_lock_recovery_timeout_when_lock_persists(monkeypatch):
    """锁关系持续存在超过截止 → needs_human(recovery_timeout)。"""
    from app.agent import nodes

    class FakeLockQueries:
        def get_lock_waiters(self, *a, **kw):
            return {"ok": True, "data": {"waits": [{"blocker_ref": "blk_1",
                "object_schema": "tracemind_business", "object_table": "inventory",
                "waiting_query_ref": "INVENTORY_RESERVATION", "wait_duration_ms": 5200}]}}

    monkeypatch.setattr("app.tools.lock_queries.get_lock_waiters",
                        FakeLockQueries().get_lock_waiters)

    class FakeClock:
        """第一次调用算 deadline,第二次调用已过截止(60s),避免真实轮询。"""
        def __init__(self):
            self.calls = 0

        def time(self):
            self.calls += 1
            base = __import__("time").time()
            return base if self.calls == 1 else base + 120

    monkeypatch.setattr(nodes, "_time", FakeClock())
    state = {"incident_id": 9, "run_id": 9, "status": "executing",
             "root_cause_code": "LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION",
             "fix_execution": {"status": "succeeded"}}
    out = nodes._verify_lock_recovery(state)
    assert out.get("recovery", {}).get("termination_reason") == "recovery_timeout"
    assert out.get("recovery", {}).get("status") == "needs_human"
