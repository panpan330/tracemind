"""V2.0-A closure:真实 Runner/Graph 的进程重启恢复集成测试。

链路:真实 Run 执行到审批 interrupt → 释放并重建 Runner/Checkpointer(模拟进程重启)
→ 同一 agent_run_id + thread_id 恢复 → 验证未创建新 Run、冻结上下文逐项不变
→ 从原审批节点继续执行至 recovered。
"""
import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.engine import get_control_engine
from app.db.models import AgentRun, Approval
from app.repositories import incident_repo, run_repo
from app.services import runner

pytestmark = pytest.mark.asyncio


def _patch_graph_deps(monkeypatch, tmp_path):
    """SCN-001 fixture 依赖(与 verify-m5 链路同构);proposal/approval 用真实落库。"""
    runner._saver = None
    monkeypatch.setattr(settings, "checkpoint_path", str(tmp_path / "cp.sqlite"))

    def fake_execute(tool, incident_id=None, **kwargs):
        if tool == "get_service_metrics":
            return {"success": True,
                    "data": {"p95Ms": 150, "sourceBackend": "fixture"}}
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
            return {"success": True, "data": {"waits": []}}
        if tool == "verify_recovery":
            return {"success": True, "data": {"status": "recovered", "latency_p95_after": 3}}
        return {"success": False, "data": None}

    class FakeMCP:
        def call_tool(self, name, incident_id, agent_run_id, **business):
            return fake_execute(name, incident_id=incident_id, **business)

    monkeypatch.setattr("app.agent.nodes.get_mcp_client", lambda: FakeMCP())
    monkeypatch.setattr("app.agent.nodes.execute_tool", fake_execute)
    # 写执行器本测试聚焦重启恢复(非写路径),按 test_runner 惯例替换
    monkeypatch.setattr("app.agent.nodes.fix_service.execute_fix",
                        lambda incident_id, fix_proposal_id, approval_id:
                        {"status": "succeeded", "fix_execution_id": 9})
    monkeypatch.setattr("app.agent.nodes.hypothesis_repo.upsert_hypothesis",
                        lambda *a, **kw: {"id": 1})
    monkeypatch.setattr("app.agent.nodes.evidence_repo.upsert_evidence",
                        lambda *a, **kw: {"id": 1})
    monkeypatch.setattr("app.agent.nodes.postmortem_repo.create_postmortem",
                        lambda incident_id, content: {"id": 1})


async def test_process_restart_resumes_same_run_from_frozen_context(
        monkeypatch, tmp_path):
    _patch_graph_deps(monkeypatch, tmp_path)
    baseline = {"top_digest": {"rows_examined": 1200}}  # 创建时采集的基线
    inc = incident_repo.create_incident("重启恢复集成", None, "high",
                                        "inventory-service",
                                        affected_service_ref="inventory-service",
                                        affected_operation_ref="INVENTORY_LOOKUP")
    inc_id = inc.id
    run = run_repo.create_run(inc_id, baseline=baseline)

    # 1) 真实图执行到审批 interrupt
    await runner.start_investigation(inc_id, run.id, run.thread_id)
    await asyncio.wait_for(runner._tasks[run.id], timeout=30)
    first = run_repo.get_run(run.id)
    assert first.status == "awaiting_approval"

    def _frozen_facts():
        with Session(get_control_engine()) as s:
            r = s.get(AgentRun, run.id)
            return {
                "service_ref": r.run_context_snapshot_json["service_ref"],
                "operation": r.run_context_snapshot_json["affected_operation_ref"],
                "baseline_ref": r.run_context_snapshot_json["baseline_ref"],
                "window": r.run_context_snapshot_json["investigation_window"],
                "versions": dict(r.run_context_snapshot_json["bundle_versions"]),
                "checkpoint": dict(r.run_context_snapshot_json["checkpoint"]),
                "frozen_at": r.run_context_snapshot_json["frozen_at"],
                "digest_baseline": r.incident_digest_baseline,
                "thread": r.thread_id,
                "ckpt_thread": r.checkpoint_thread_id,
            }

    before = _frozen_facts()
    assert before["service_ref"] == "inventory-service"
    assert before["versions"]["policy"]  # 四类版本已在创建事务冻结

    # 审批行绑定本 Run(真实 propose_fix 落库)
    with Session(get_control_engine()) as s:
        ap = s.scalars(select(Approval).where(
            Approval.incident_id == inc_id).order_by(Approval.id.desc())).first()
        assert ap is not None and ap.agent_run_id == run.id

    # 2) 模拟进程重启:释放并重建 Checkpointer(同 checkpoint 文件),清空内存任务
    runner._saver = None
    runner._tasks.clear()

    # 3) 同一 agent_run_id + thread_id 恢复(审批决定),不创建新 Run。
    # 恢复前按生产路径 CAS 批准真实 approval 行(否则 execute_fix fail closed 拒绝执行)
    from app.db.models import utcnow

    from app.repositories import approval_repo
    with Session(get_control_engine()) as s:
        ap_row = s.scalars(select(Approval).where(
            Approval.incident_id == inc_id).order_by(Approval.id.desc())).first()
        ap_id = ap_row.id
    decided = approval_repo.decide_approval_cas(
        ap_id, decision="approved", approver="restart-test", comment=None,
        now_utc=utcnow())
    assert decided is not None
    await runner.resume_investigation(run.thread_id, {"decision": "approved"})

    with Session(get_control_engine()) as s:
        runs_for_incident = s.scalars(select(AgentRun).where(
            AgentRun.incident_id == inc_id)).all()
        assert len(runs_for_incident) == 1  # 未重新创建 Run
        final = s.get(AgentRun, run.id)

    # 4) 冻结上下文逐项不变(service/operation/baseline/窗口/四类版本/冻结时间)
    after = _frozen_facts()
    assert after == before
    assert final.status == "recovered"          # 5) 从原审批节点继续执行完成
    assert final.thread_id == before["thread"]
    assert final.checkpoint_thread_id == before["ckpt_thread"]
    assert incident_repo.get_incident(inc_id).status == "recovered"

    # 版本冻结值未被收尾覆盖
    with Session(get_control_engine()) as s:
        r = s.get(AgentRun, run.id)
        assert r.expected_policy_bundle_version == before["versions"]["policy"]


async def test_restart_recovery_survives_incident_row_update(monkeypatch, tmp_path):
    """重启期间 Incident 行被更新:恢复仍使用冻结快照上下文(不重新推导)。"""
    _patch_graph_deps(monkeypatch, tmp_path)
    inc = incident_repo.create_incident("重启期间变更", None, "high",
                                        "inventory-service",
                                        affected_service_ref="inventory-service",
                                        affected_operation_ref="INVENTORY_LOOKUP")
    inc_id = inc.id
    run = run_repo.create_run(inc_id)
    await runner.start_investigation(inc_id, run.id, run.thread_id)
    await asyncio.wait_for(runner._tasks[run.id], timeout=30)

    runner._saver = None
    runner._tasks.clear()
    # 模拟重启窗口内 Incident 行被人工更新(换 service/operation)
    from sqlalchemy import text

    with Session(get_control_engine()) as s:
        s.execute(text("UPDATE incident SET service_ref='order-service', "
                       "affected_operation_ref='ORDER_CREATE' WHERE id=:i"),
                  {"i": inc_id})
        s.commit()

    from app.db.models import utcnow

    from app.repositories import approval_repo
    with Session(get_control_engine()) as s:
        ap_row = s.scalars(select(Approval).where(
            Approval.incident_id == inc_id).order_by(Approval.id.desc())).first()
    approval_repo.decide_approval_cas(ap_row.id, decision="approved",
                                      approver="restart-test", comment=None,
                                      now_utc=utcnow())
    await runner.resume_investigation(run.thread_id, {"decision": "approved"})
    with Session(get_control_engine()) as s:
        r = s.get(AgentRun, run.id)
        snap = r.run_context_snapshot_json
        assert snap["service_ref"] == "inventory-service"        # 冻结值
        assert snap["affected_operation_ref"] == "INVENTORY_LOOKUP"  # 冻结值,非更新值
        assert r.status == "recovered"
