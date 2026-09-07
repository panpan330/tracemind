"""V2.1-B:Dispatcher 租约调度 —— CAS 领取/租约回收/容量/重启窗口(特征测试先行)。"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun, Incident
from app.repositories import run_repo
from app.services import dispatcher, runner


def _drain_stale_queued():
    """共享测试库中存在历史 queued 残留(live/先前测试产物):置 cancelled,
    保证断言从确定状态开始(生产语义不变:最老 queued 优先)。"""
    with Session(get_control_engine()) as s:
        s.execute(text(
            "UPDATE agent_run SET status='cancelled', active_run_key=NULL, "
            "lease_owner=NULL, lease_until=NULL, dispatch_status='DISPATCHED' "
            "WHERE status='queued'"))
        s.commit()


def _mk_queued(*, dispatch_status="READY", lease_owner=None, lease_until=None,
               trigger_source="alertmanager", baseline=None, drain=True):
    if drain:
        _drain_stale_queued()
    """直接构造 queued Run(含冻结快照与 active_run_key,绕过聚合事务)。"""
    with Session(get_control_engine()) as s:
        inc = Incident(title=f"dq-{uuid.uuid4().hex[:6]}", severity="high",
                       service_ref="inventory-service",
                       affected_operation_ref="INVENTORY_LOOKUP",
                       source="alertmanager", alert_name="OrderOperationP95High",
                       environment="demo", alert_status="FIRING",
                       lifecycle_status="OPEN")
        s.add(inc)
        s.commit()
        s.refresh(inc)
    run = run_repo.create_run(inc.id, baseline=baseline, trigger_source=trigger_source,
                              status="queued")
    with Session(get_control_engine()) as s:
        s.execute(text(
            "UPDATE agent_run SET dispatch_status=:ds, lease_owner=:o, lease_until=:lu "
            "WHERE id=:i"), {"ds": dispatch_status, "o": lease_owner, "lu": lease_until,
                             "i": run.id})
        s.commit()
    return inc.id, run.id


def _run_row(run_id):
    with Session(get_control_engine()) as s:
        return s.execute(text(
            "SELECT status, dispatch_status, lease_owner, lease_until, dispatch_attempts "
            "FROM agent_run WHERE id=:i"), {"i": run_id}).fetchone()


def test_claim_single_cas_wins():
    inc, run_id = _mk_queued()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    claimed = run_repo.claim_queued_run("owner-a", lease_seconds=30, now=now)
    assert claimed is not None and claimed.id == run_id
    row = _run_row(run_id)
    assert row.dispatch_status == "CLAIMED" and row.lease_owner == "owner-a"
    assert row.dispatch_attempts == 1 and row.status == "queued"   # 租约期 status 不变
    assert run_repo.claim_queued_run("owner-b", lease_seconds=30, now=now) is None


def test_expired_claimed_lease_is_reclaimable():
    inc, run_id = _mk_queued(dispatch_status="CLAIMED", lease_owner="dead",
                             lease_until=datetime.now(timezone.utc).replace(tzinfo=None)
                             - timedelta(seconds=1))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    claimed = run_repo.claim_queued_run("owner-new", lease_seconds=30, now=now)
    assert claimed is not None and claimed.id == run_id
    row = _run_row(run_id)
    assert row.lease_owner == "owner-new" and row.dispatch_attempts == 1


def test_unexpired_claimed_lease_not_reclaimable():
    inc, run_id = _mk_queued(dispatch_status="CLAIMED", lease_owner="alive",
                             lease_until=datetime.now(timezone.utc).replace(tzinfo=None)
                             + timedelta(seconds=30))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert run_repo.claim_queued_run("owner-b", lease_seconds=30, now=now) is None


def test_dispatched_never_reclaimed():
    inc, run_id = _mk_queued(dispatch_status="DISPATCHED",
                             lease_until=datetime.now(timezone.utc).replace(tzinfo=None)
                             - timedelta(seconds=30))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert run_repo.claim_queued_run("owner-x", lease_seconds=30, now=now) is None


def test_mark_dispatched_requires_owner_and_valid_lease():
    inc, run_id = _mk_queued(dispatch_status="CLAIMED", lease_owner="owner-a",
                             lease_until=datetime.now(timezone.utc).replace(tzinfo=None)
                             + timedelta(seconds=30))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert run_repo.mark_dispatched(run_id, "owner-b", now) is False      # 非 owner
    future = now + timedelta(seconds=60)
    assert run_repo.mark_dispatched(run_id, "owner-a", future) is False   # 租约已过期
    assert run_repo.mark_dispatched(run_id, "owner-a", now) is True       # owner + 未过期
    assert _run_row(run_id).status == "investigating"
    assert _run_row(run_id).dispatch_status == "DISPATCHED"


def test_lease_expiry_blocks_original_owner_start():
    """租约过期后原 owner 不得启动图(CAS 拒绝)。"""
    inc, run_id = _mk_queued(dispatch_status="CLAIMED", lease_owner="owner-a",
                             lease_until=datetime.now(timezone.utc).replace(tzinfo=None)
                             - timedelta(seconds=5))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert run_repo.mark_dispatched(run_id, "owner-a", now) is False


# ---------- Dispatcher loop 集成(显式开启;真实 runner 启动图) ----------

def _patch_graph_deps(monkeypatch, tmp_path, slow_gate=None):
    runner._saver = None
    monkeypatch.setattr(runner.settings, "checkpoint_path", str(tmp_path / "cp.sqlite"))
    monkeypatch.setattr(runner.settings, "dispatch_interval_seconds", 0.05)
    monkeypatch.setattr(runner.settings, "dispatch_lease_seconds", 30)
    monkeypatch.setattr(runner.settings, "dispatch_enabled", True)

    def fake_execute(tool, incident_id=None, **kwargs):
        if tool == "get_service_metrics":
            return {"success": True, "data": {"p95Ms": 150, "sourceBackend": "fixture"}}
        if tool == "get_trace":
            return {"success": True, "data": {"sourceBackend": "fixture",
                    "inventoryServerDurationMs": 90, "targetDbDurationMs": 85,
                    "dbDominanceRatio": 0.9, "targetDbSpanId": "s3",
                    "normalizationRuleVersion": "TRACE_NORMALIZER_V1"}}
        if tool == "list_expensive_query_digests":
            # 自动 Run 无基线 → BASELINE_INSUFFICIENT(E3 unknown 语义)
            return {"success": False, "error_code": "BASELINE_INSUFFICIENT", "data": None}
        if tool == "get_query_plan":
            return {"success": True,
                    "data": {"explain": {"query_block": {"table": {"access_type": "ALL"}}}}}
        if tool == "get_index_info":
            return {"success": True, "data": {"indexes": [{"index_name": "PRIMARY"}]}}
        if tool == "get_lock_waiters":
            return {"success": True, "data": {"waits": []}}
        return {"success": False, "data": None}

    class FakeMCP:
        def call_tool(self, name, incident_id, agent_run_id, **business):
            return fake_execute(name, incident_id=incident_id, **business)

    monkeypatch.setattr("app.agent.nodes.get_mcp_client", lambda: FakeMCP())
    monkeypatch.setattr("app.agent.nodes.execute_tool", fake_execute)
    monkeypatch.setattr("app.agent.nodes.hypothesis_repo.upsert_hypothesis",
                        lambda *a, **kw: {"id": 1})
    monkeypatch.setattr("app.agent.nodes.evidence_repo.upsert_evidence",
                        lambda *a, **kw: {"id": 1})
    monkeypatch.setattr("app.agent.nodes.postmortem_repo.create_postmortem",
                        lambda incident_id, content: {"id": 1})


@pytest.mark.asyncio
async def test_dispatcher_claims_and_starts_graph(monkeypatch, tmp_path):
    _patch_graph_deps(monkeypatch, tmp_path)
    _drain_stale_queued()
    inc_id, run_id = _mk_queued()

    task = asyncio.create_task(dispatcher.dispatch_loop())
    for _ in range(60):
        row = _run_row(run_id)
        if row.dispatch_status == "DISPATCHED":
            break
        await asyncio.sleep(0.1)
    assert row.dispatch_status == "DISPATCHED"   # 图被真实启动(E3 unknown → 不确认根因)
    for _ in range(450):                          # E3 每轮重试 2s,预算 14 轮 ≈ 40s
        row = _run_row(run_id)
        if row.status in ("needs_human", "recovered", "failed"):
            break
        await asyncio.sleep(0.2)
    row = _run_row(run_id)
    assert row.status == "needs_human"           # 无基线 → E3 unknown → 不确认根因
    # 没有创建第二个 Run(active_run_key 唯一 + 单自动 Run 语义)
    with Session(get_control_engine()) as s:
        n_runs = s.execute(text("SELECT COUNT(*) FROM agent_run WHERE incident_id=:i"),
                           {"i": inc_id}).scalar()
        key = s.execute(text("SELECT active_run_key FROM agent_run WHERE id=:i"),
                        {"i": run_id}).scalar()
    assert n_runs == 1
    assert key is None                            # 终态后集中清键
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_capacity_blocks_second_run_until_first_terminal(monkeypatch, tmp_path):
    """两个 queued Run:第一个图被阻塞时多次 tick,第二个保持 queued;
    第一个终态释放容量后第二个才被领取(真实容量计算,非单轮限领)。"""
    _patch_graph_deps(monkeypatch, tmp_path)
    import threading

    release = threading.Event()

    def blocking_metrics(tool, incident_id=None, **kwargs):
        if tool == "get_service_metrics":
            # 阻塞第一个图的工作线程,直到测试放行(Event 为线程事件)
            release.wait(timeout=20)
            return {"success": True, "data": {"p95Ms": 150, "sourceBackend": "fixture"}}
        if tool == "get_trace":
            return {"success": True, "data": {"sourceBackend": "fixture",
                    "inventoryServerDurationMs": 90, "targetDbDurationMs": 85,
                    "dbDominanceRatio": 0.9, "targetDbSpanId": "s3",
                    "normalizationRuleVersion": "TRACE_NORMALIZER_V1"}}
        if tool == "list_expensive_query_digests":
            return {"success": False, "error_code": "BASELINE_INSUFFICIENT", "data": None}
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
            return blocking_metrics(name, incident_id=incident_id, **business)

    monkeypatch.setattr("app.agent.nodes.get_mcp_client", lambda: FakeMCP())
    monkeypatch.setattr("app.agent.nodes.execute_tool", blocking_metrics)
    monkeypatch.setattr("app.agent.nodes.hypothesis_repo.upsert_hypothesis",
                        lambda *a, **kw: {"id": 1})
    monkeypatch.setattr("app.agent.nodes.evidence_repo.upsert_evidence",
                        lambda *a, **kw: {"id": 1})
    monkeypatch.setattr("app.agent.nodes.postmortem_repo.create_postmortem",
                        lambda incident_id, content: {"id": 1})
    monkeypatch.setattr("app.agent.nodes.proposal_repo.create_proposal",
                        lambda **kw: type("P", (), {"id": 1})())
    monkeypatch.setattr("app.agent.nodes.approval_repo.create_approval",
                        lambda **kw: type("A", (), {"id": 5, "status": "pending",
                                                    "agent_run_id": 1})())

    _drain_stale_queued()
    inc1, run1 = _mk_queued(drain=False)
    inc2, run2 = _mk_queued(drain=False)

    task = asyncio.create_task(dispatcher.dispatch_loop())
    for _ in range(80):
        r1, r2 = _run_row(run1), _run_row(run2)
        if r1.dispatch_status == "DISPATCHED" and run1 in runner._tasks:
            break
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.6)                     # 多次 tick
    assert _run_row(run2).status == "queued"     # 容量被占,第二个保持 queued
    assert _run_row(run2).dispatch_status == "READY"

    await asyncio.sleep(0.1)
    release.set()                                # 释放第一个图
    for _ in range(300):
        r2 = _run_row(run2)
        if r2.dispatch_status == "DISPATCHED":
            break
        await asyncio.sleep(0.1)
    assert _run_row(run2).dispatch_status == "DISPATCHED"   # 容量释放后才领取

    for rid in (run1, run2):
        if rid in runner._tasks:
            try:
                await asyncio.wait_for(runner._tasks[rid], timeout=60)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
