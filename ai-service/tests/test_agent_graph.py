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
                    "data": {"p95Ms": 100, "representativeSlowTraceId": "t1"}}
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
        return {"success": False, "data": None}

    def fake_create_proposal(**kwargs):
        return FakeProposal()

    monkeypatch.setattr("app.agent.nodes.execute_tool", fake_execute)
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

    monkeypatch.setattr("app.agent.nodes.execute_tool", fake_execute)
    monkeypatch.setattr("app.agent.nodes.hypothesis_repo.upsert_hypothesis",
                        lambda *a, **kw: {"id": 1})
    monkeypatch.setattr("app.agent.nodes.evidence_repo.upsert_evidence",
                        lambda *a, **kw: {"id": 1})
    graph = build_graph()
    state = {"incident_id": 2, "service_ref": "inventory-service",
             "severity": "high", "max_investigation_rounds": 1, "max_tool_calls": 5}
    result = graph.invoke(state)
    assert result["status"] == "needs_human"
    assert result["termination_reason"] == "evidence_budget_exhausted"
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
