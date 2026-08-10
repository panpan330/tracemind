"""Task 3.6: 后台执行模型(asyncio.Task + checkpoint 恢复)。"""
import asyncio

import pytest

from app.config import settings
from app.repositories import incident_repo, run_repo
from app.services import runner

pytestmark = pytest.mark.asyncio


def _patch_graph_deps(monkeypatch, tmp_path):
    runner._saver = None  # 重置全局 saver,指向临时 checkpoint
    monkeypatch.setattr(settings, "checkpoint_path", str(tmp_path / "cp.sqlite"))

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
    monkeypatch.setattr("app.agent.nodes.proposal_repo.create_proposal",
                        lambda **kw: type("P", (), {"id": 1})())
    monkeypatch.setattr("app.agent.nodes.approval_repo.create_approval",
                        lambda **kw: type("A", (), {"id": 2})())


async def test_start_investigation_runs_graph_to_interrupt(monkeypatch, tmp_path):
    _patch_graph_deps(monkeypatch, tmp_path)
    inc = incident_repo.create_incident("runner 测试", None, "high", "inventory-service")
    run = run_repo.create_run(inc.id)

    await runner.start_investigation(inc.id, run.id, run.thread_id)
    # 启动即标记 investigating
    assert run_repo.get_run(run.id).status == "investigating"

    # 等待后台任务完成:图停在审批 interrupt(awaiting_approval)
    task = runner._tasks[run.id]
    await asyncio.wait_for(task, timeout=20)
    assert run_repo.get_run(run.id).status == "awaiting_approval"


async def test_recover_pending_runs_resumes_interrupted(monkeypatch, tmp_path):
    _patch_graph_deps(monkeypatch, tmp_path)
    inc = incident_repo.create_incident("recover 测试", None, "medium", "inventory-service")
    run = run_repo.create_run(inc.id)
    run_repo.update_run_status(run.id, "investigating")  # 模拟服务重启残留

    await runner.recover_pending_runs()
    assert run.id in runner._tasks
    task = runner._tasks[run.id]
    await asyncio.wait_for(task, timeout=20)
    assert run_repo.get_run(run.id).status == "awaiting_approval"
