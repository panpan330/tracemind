"""状态变化事件写入与 SSE 终态关闭。"""
import asyncio

import pytest
from sqlalchemy import select

from app.agent import nodes
from app.db.engine import get_control_engine
from app.db.models import IncidentEvent
from app.repositories import incident_repo, run_repo
from app.services import runner

pytestmark = pytest.mark.asyncio


def _patch_graph_deps(monkeypatch, tmp_path):
    runner._saver = None
    from app.config import settings
    monkeypatch.setattr(settings, "checkpoint_path", str(tmp_path / "cp.sqlite"))

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
        return {"success": False, "data": None}

    class FakeMCP:
        def call_tool(self, name, incident_id, agent_run_id, **business):
            return fake_execute(name, incident_id=incident_id, **business)

    monkeypatch.setattr("app.agent.nodes.get_mcp_client", lambda: FakeMCP())
    monkeypatch.setattr("app.agent.nodes.proposal_repo.create_proposal",
                        lambda **kw: type("P", (), {"id": 1})())
    monkeypatch.setattr("app.agent.nodes.approval_repo.create_approval",
                        lambda **kw: type("A", (), {"id": 2})())
    monkeypatch.setattr("app.agent.nodes.hypothesis_repo.upsert_hypothesis",
                        lambda *a, **kw: {"id": 1})
    monkeypatch.setattr("app.agent.nodes.evidence_repo.upsert_evidence",
                        lambda *a, **kw: {"id": 1})
    monkeypatch.setattr("app.agent.nodes.postmortem_repo.create_postmortem",
                        lambda *a, **kw: {"id": 1})
    # 健康基线:测试环境 Incident 未采集,走宽松阈值,无需 mock incident_repo


async def _wait_terminal(run_id: int, timeout_s: float = 15.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        run = run_repo.get_run(run_id)
        if run.status not in {"created", "investigating", "executing", "verifying"}:
            return
        await asyncio.sleep(0.2)
    raise AssertionError(f"run {run_id} 未在 {timeout_s}s 内到达终态")


def _event_types(incident_id: int) -> list[str]:
    with get_control_engine().connect() as conn:
        return [e.event_type for e in conn.execute(
            select(IncidentEvent).where(IncidentEvent.incident_id == incident_id))]


async def test_start_investigation_emits_status_events(monkeypatch, tmp_path):
    _patch_graph_deps(monkeypatch, tmp_path)
    inc = incident_repo.create_incident("evt-test", None, "low", "inventory-service")
    run = run_repo.create_run(inc.id)
    await runner.start_investigation(inc.id, run.id, run.thread_id)
    await _wait_terminal(run.id)

    types = _event_types(inc.id)
    assert "status_changed" in types
    # 至少一条调查中 + 一条到 awaiting_approval 的状态事件
    with get_control_engine().connect() as conn:
        payloads = [e.payload for e in conn.execute(
            select(IncidentEvent).where(
                IncidentEvent.incident_id == inc.id,
                IncidentEvent.event_type == "status_changed"))]
    statuses = [p["status"] for p in payloads if p and "status" in p]
    assert "investigating" in statuses
    assert "awaiting_approval" in statuses
