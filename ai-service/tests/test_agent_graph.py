from app.agent.graph import build_graph


def test_graph_compiles():
    graph = build_graph()
    assert graph is not None


def test_full_evidence_path_confirms(monkeypatch):
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

    monkeypatch.setattr("app.agent.nodes.execute_tool", fake_execute)
    graph = build_graph()
    state = {"incident_id": 1, "service_ref": "inventory-service", "severity": "high"}
    result = graph.invoke(state)
    assert result["confirmed_hypothesis_id"] == "h1"
    gate = result["evidence_gate"]
    assert all(gate[k] for k in ("E1", "E2", "E3", "E4", "E5"))
