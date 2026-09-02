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
                    "data": {"p95Ms": 150, "representativeSlowTraceId": "t1"}}
        if tool == "get_trace":
            return {"success": True, "data": {"sourceBackend": "fixture",
                "inventoryServerDurationMs": 90, "targetDbDurationMs": 85,
                "dbDominanceRatio": 0.9, "targetDbSpanId": "s3",
                "normalizationRuleVersion": "TRACE_NORMALIZER_V1"}}
        if tool == "list_expensive_query_digests":
            return {"success": True,
                    "data": [{"rows_examined_delta": 100_000, "count_delta": 10}]}
        if tool == "get_query_plan":
            return {"success": True,
                    "data": {"explain": {"query_block": {"table": {"access_type": "ALL"}}}}}
        if tool == "get_index_info":
            return {"success": True, "data": {"indexes": [{"index_name": "PRIMARY"}]}}
        if tool == "get_lock_waiters":
            return {"success": True, "data": {"observed_at": "2026-08-11T00:00:00Z",
                "snapshot_expires_at": "2026-08-11T00:00:20Z", "waits": []}}
        if tool == "get_transaction_details":
            return {"success": False, "data": None, "error_message": "TRX_NOT_FOUND"}
        if tool == "verify_recovery":
            return {"success": True, "data": {"status": "recovered", "latency_p95_after": 3}}
        return {"success": False, "data": None}

    def fake_create_postmortem(incident_id, content):
        return {"id": 1}

    monkeypatch.setattr("app.agent.nodes.approval_repo.create_approval", fake_create_approval)
    monkeypatch.setattr("app.agent.nodes.fix_service.execute_fix", fake_execute_fix)
    class FakeMCP:
        def call_tool(self, name, incident_id, agent_run_id, **business):
            return fake_execute(name, incident_id=incident_id, **business)

    monkeypatch.setattr("app.agent.nodes.get_mcp_client", lambda: FakeMCP())
    # verify_recovery 属确定性安全控制节点(不纳入 MCP),直接调 execute_tool
    monkeypatch.setattr("app.agent.nodes.execute_tool", fake_execute)
    monkeypatch.setattr("app.agent.nodes.postmortem_repo.create_postmortem", fake_create_postmortem)
    monkeypatch.setattr("app.agent.nodes.hypothesis_repo.upsert_hypothesis",
                        lambda *a, **kw: {"id": 1})
    monkeypatch.setattr("app.agent.nodes.evidence_repo.upsert_evidence",
                        lambda *a, **kw: {"id": 1})


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
            agent_run_id=7,
        )

    def fake_decide_cas(approval_id, *, decision, approver, comment, now_utc):
        calls["cas"] = {"decision": decision, "approver": approver,
                        "comment": comment, "now_utc": now_utc}
        return SimpleNamespace(id=approval_id, incident_id=1, status=decision)

    def fake_get_run(run_id):
        assert run_id == 7  # 恢复绑定审批所属 Run,而非 incident 最近 Run
        return SimpleNamespace(id=7, thread_id="run-1")

    async def fake_resume(thread_id, resume_value):
        calls["resume"] = {"thread_id": thread_id, "value": resume_value}

    monkeypatch.setattr("app.api.approvals.approval_repo.get_approval", fake_get_approval)
    monkeypatch.setattr("app.api.approvals.approval_repo.decide_approval_cas", fake_decide_cas)
    monkeypatch.setattr("app.api.approvals.run_repo.get_run", fake_get_run)
    monkeypatch.setattr("app.api.approvals.resume_investigation", fake_resume)

    resp = _api_client.post("/api/incidents/1/approvals/5/decision",
                            json={"decision": "approved", "comment": "同意"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["approved_by"] == "demo-approver"
    assert calls["cas"]["decision"] == "approved"
    assert calls["cas"]["approver"] == "demo-approver"
    assert calls["cas"]["now_utc"] is not None  # 应用生成 now_utc,不依赖 DB NOW()
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
            agent_run_id=None,
        )

    monkeypatch.setattr("app.api.approvals.approval_repo.get_approval", fake_get_approval)
    monkeypatch.setattr("app.api.approvals.approval_repo.decide_approval_cas",
                        lambda *a, **kw: None)  # CAS 失败(过期)
    resp = _api_client.post("/api/incidents/1/approvals/5/decision",
                            json={"decision": "approved"})
    assert resp.status_code == 409


def test_decision_already_processed(monkeypatch):
    def fake_get_approval(approval_id):
        return SimpleNamespace(
            id=approval_id, incident_id=1, status="approved",
            expires_at=datetime.utcnow() + timedelta(minutes=5),
            agent_run_id=None,
        )

    monkeypatch.setattr("app.api.approvals.approval_repo.get_approval", fake_get_approval)
    monkeypatch.setattr("app.api.approvals.approval_repo.decide_approval_cas",
                        lambda *a, **kw: None)  # CAS 失败(已决定)
    resp = _api_client.post("/api/incidents/1/approvals/5/decision",
                            json={"decision": "approved"})
    assert resp.status_code == 409
