"""Task 3.4: 审批中断与恢复(interrupt / Command(resume)/ 过期)。"""
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agent.graph import build_graph


class FakeApproval:
    id = 5
    status = "pending"


def _patch_node_deps(monkeypatch):
    def fake_create_approval(**kwargs):
        return FakeApproval()

    def fake_execute_fix(incident_id, fix_proposal_id, approval_id):
        return {"status": "succeeded", "fix_execution_id": 9}

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
        if tool == "verify_recovery":
            return {"success": True, "data": {"status": "recovered", "latency_p95_after": 3}}
        return {"success": False, "data": None}

    def fake_create_postmortem(incident_id, content):
        return {"id": 1}

    monkeypatch.setattr("app.agent.nodes.approval_repo.create_approval", fake_create_approval)
    monkeypatch.setattr("app.agent.nodes.fix_service.execute_fix", fake_execute_fix)
    monkeypatch.setattr("app.agent.nodes.execute_tool", fake_execute)
    monkeypatch.setattr("app.agent.nodes.postmortem_repo.create_postmortem", fake_create_postmortem)


def _run_to_interrupt(graph, config):
    state = {"incident_id": 1, "service_ref": "inventory-service", "severity": "high",
             "max_investigation_rounds": 1, "max_tool_calls": 5}
    return graph.invoke(state, config=config)


def test_approval_interrupt_then_resume_approved(monkeypatch):
    _patch_node_deps(monkeypatch)
    graph = build_graph(checkpointer=InMemorySaver())
    config = {"thread_id": "t1", "recursion_limit": 100}

    first = _run_to_interrupt(graph, config)
    # 调查完成并停在审批挂起点
    assert first["confirmed_hypothesis_id"] == "h1"
    assert first["status"] == "awaiting_approval"
    assert first["approval"]["status"] == "pending"
    assert first["approval"]["approval_id"] == 5
    assert first["fix_proposal"]["action_type"] == "CREATE_INVENTORY_INDEX"

    second = graph.invoke(Command(resume={"decision": "approved"}), config=config)
    # 批准后执行修复并验证恢复
    assert second["status"] == "recovered"
    assert second["fix_execution"]["fix_execution_id"] == 9
    assert second["fix_execution"]["status"] == "succeeded"
    assert second["recovery"]["status"] == "recovered"
    assert second["report"]["content"]  # 复盘报告已生成


def test_approval_rejected_ends_with_report(monkeypatch):
    _patch_node_deps(monkeypatch)
    graph = build_graph(checkpointer=InMemorySaver())
    config = {"thread_id": "t2", "recursion_limit": 100}

    first = _run_to_interrupt(graph, config)
    assert first["status"] == "awaiting_approval"

    second = graph.invoke(
        Command(resume={"decision": "rejected", "comment": "暂不处置"}), config=config)
    assert second["status"] == "rejected"
    assert second["approval"]["status"] == "rejected"
    assert second["report"]["content"]


def test_approval_requires_checkpoint_thread():
    # 无 checkpointer 时 interrupt 无法恢复(编译期允许,运行期由调用方提供)
    graph = build_graph()  # 不传 checkpointer 也可编译
    assert graph is not None


# ---------- 审批 API 端点(校验 + 状态更新 + 恢复调用) ----------
from datetime import datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app

_api_client = TestClient(app)


def test_decision_approved_updates_approval_and_resumes(monkeypatch):
    calls = {}

    def fake_get_approval(approval_id):
        return SimpleNamespace(
            id=approval_id, incident_id=1, status="pending",
            expires_at=datetime.utcnow() + timedelta(minutes=5),
        )

    def fake_update_approval(approval_id, *, status, approver, comment, consumed_at=None):
        calls["update"] = {"status": status, "approver": approver, "comment": comment}
        return None

    def fake_list_runs(incident_id):
        return [SimpleNamespace(thread_id="run-1")]

    async def fake_resume(thread_id, resume_value):
        calls["resume"] = {"thread_id": thread_id, "value": resume_value}

    monkeypatch.setattr("app.api.approvals.approval_repo.get_approval", fake_get_approval)
    monkeypatch.setattr("app.api.approvals.approval_repo.update_approval", fake_update_approval)
    monkeypatch.setattr("app.api.approvals.run_repo.list_runs", fake_list_runs)
    monkeypatch.setattr("app.api.approvals.resume_investigation", fake_resume)

    resp = _api_client.post("/api/incidents/1/approvals/5/decision",
                            json={"decision": "approved", "comment": "同意"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["approved_by"] == "demo-approver"
    assert calls["update"]["status"] == "approved"
    assert calls["update"]["approver"] == "demo-approver"
    assert calls["resume"]["thread_id"] == "run-1"
    assert calls["resume"]["value"]["decision"] == "approved"


def test_decision_invalid_value_rejected(monkeypatch):
    def fake_get_approval(approval_id):
        return SimpleNamespace(
            id=approval_id, incident_id=1, status="pending",
            expires_at=datetime.utcnow() + timedelta(minutes=5),
        )

    monkeypatch.setattr("app.api.approvals.approval_repo.get_approval", fake_get_approval)
    resp = _api_client.post("/api/incidents/1/approvals/5/decision",
                            json={"decision": "maybe"})
    assert resp.status_code == 422


def test_decision_approval_not_found(monkeypatch):
    monkeypatch.setattr("app.api.approvals.approval_repo.get_approval",
                        lambda approval_id: None)
    resp = _api_client.post("/api/incidents/9/approvals/99/decision",
                            json={"decision": "approved"})
    assert resp.status_code == 404


def test_decision_approval_expired(monkeypatch):
    def fake_get_approval(approval_id):
        return SimpleNamespace(
            id=approval_id, incident_id=1, status="pending",
            expires_at=datetime.utcnow() - timedelta(minutes=1),
        )

    monkeypatch.setattr("app.api.approvals.approval_repo.get_approval", fake_get_approval)
    resp = _api_client.post("/api/incidents/1/approvals/5/decision",
                            json={"decision": "approved"})
    assert resp.status_code == 409


def test_decision_already_processed(monkeypatch):
    def fake_get_approval(approval_id):
        return SimpleNamespace(
            id=approval_id, incident_id=1, status="approved",
            expires_at=datetime.utcnow() + timedelta(minutes=5),
        )

    monkeypatch.setattr("app.api.approvals.approval_repo.get_approval", fake_get_approval)
    resp = _api_client.post("/api/incidents/1/approvals/5/decision",
                            json={"decision": "approved"})
    assert resp.status_code == 409
