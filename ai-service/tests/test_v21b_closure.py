"""V2.1-B closure:修复项的回归测试。

1. lifespan 任务管理(dispatch_enabled 两种配置,无泄漏/无 AttributeError;
   recover 阶段异常时 MCP Manager 仍必须被 stop);
2. touched_firing 不重复关联(occurrence 按矩阵);
3. 映射失败(resolve_alert → None)不得改写聚合 Incident(FIRING/RESOLVED 两条路径);
4. 共享 Incident 并发聚合(真实线程并发,行锁保证);
5. DISPATCHED 后启动失败 → 回退 queued 安全重试;
6. 图执行门统一串行(max_concurrent_runs 覆盖 recover/resume)+ 图初始化异常
   不使 Run/Incident 永久处于进行中。
"""
import asyncio
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.engine import get_control_engine
from app.db.models import AgentRun, Incident
from app.incident_gateway import service as gateway_service
from app.incident_gateway.schemas import AlertmanagerWebhookIn
from app.main import app as fastapi_app
from app.repositories import incident_repo, run_repo
from tests.test_dispatcher import _drain_stale_queued, _mk_queued, _run_row
from app.services import dispatcher, runner
from langgraph.types import Command


def _payload(*, fp=None, starts_at="2026-09-06T05:00:00.000Z", status="firing",
             annotations=None, ends_at=None, service="order-service",
             operation="ORDER_CREATE"):
    return {
        "version": "4", "groupKey": "g", "status": status, "receiver": "tracemind",
        "alerts": [{
            "status": status,
            "labels": {"alertname": "OrderOperationP95High", "service": service,
                       "operation": operation, "environment": "demo"},
            "annotations": annotations or {"summary": "t"},
            "startsAt": starts_at, "endsAt": ends_at,
            "fingerprint": fp or uuid.uuid4().hex[:12],
        }],
    }


def _process(payload):
    return gateway_service.process_alert_batch(
        "alertmanager", AlertmanagerWebhookIn.model_validate(payload))


def _purge_demo_group():
    gk = "unused-placeholder"
    from app.incident_gateway.registry import group_key_hash
    gk = group_key_hash("alertmanager", "OrderOperationP95High", "demo",
                        "order-service", "ORDER_CREATE")
    with Session(get_control_engine()) as s:
        s.execute(text(
            "DELETE agent_run FROM agent_run JOIN incident ON "
            "agent_run.incident_id = incident.id WHERE incident.group_key = :g"),
            {"g": gk})
        s.execute(text(
            "DELETE incident_alert FROM incident_alert JOIN incident ON "
            "incident_alert.incident_id = incident.id WHERE incident.group_key = :g"),
            {"g": gk})
        s.execute(text("DELETE FROM incident WHERE group_key = :g"), {"g": gk})
        s.commit()


def _incident_for(fp):
    with Session(get_control_engine()) as s:
        return s.execute(text(
            "SELECT i.id, i.occurrence_count, i.alert_status, i.lifecycle_status "
            "FROM incident i JOIN incident_alert ia ON ia.incident_id = i.id "
            "JOIN alert_instance ai ON ai.alert_instance_key = ia.alert_instance_key "
            "WHERE ai.external_fingerprint = :f"), {"f": fp}).fetchone()


def _links_for(inc_id):
    with Session(get_control_engine()) as s:
        return s.execute(text(
            "SELECT ia.alert_instance_key FROM incident_alert ia "
            "WHERE ia.incident_id = :i"), {"i": inc_id}).fetchall()


def _runs_for(inc_id):
    with Session(get_control_engine()) as s:
        return s.execute(text(
            "SELECT id, status, trigger_source, dispatch_status FROM agent_run "
            "WHERE incident_id = :i ORDER BY id"), {"i": inc_id}).fetchall()


def _instance_status(fp):
    with Session(get_control_engine()) as s:
        return s.scalar(text(
            "SELECT current_status FROM alert_instance "
            "WHERE external_fingerprint = :f"), {"f": fp})


# ---------- 1) lifespan 任务管理 ----------

@pytest.mark.parametrize("dispatch_enabled", [True, False])
def test_lifespan_manages_background_tasks(monkeypatch, dispatch_enabled):
    """两种配置下 lifespan 均正常启停:无 AttributeError、任务被 cancel、MCP 必停。"""
    monkeypatch.setattr(settings, "dispatch_enabled", dispatch_enabled)
    with TestClient(fastapi_app) as client:
        st = getattr(fastapi_app.state, "scanner_task", None)
        dt = getattr(fastapi_app.state, "dispatch_task", None)
        assert st is not None and not st.done()
        if dispatch_enabled:
            assert dt is not None and not dt.done()
        else:
            assert dt is None
    # 退出后:两个后台任务均被取消(无泄漏),MCP Manager 已 stop
    assert st.cancelled() or st.done()
    if dispatch_enabled:
        assert dt.cancelled() or dt.done()
    import app.main as main_mod
    assert main_mod.mcp_manager is None


def test_lifespan_stops_mcp_when_recover_fails(monkeypatch):
    """恢复阶段异常时清理边界必须已生效:MCP Manager 被真正 stop()、
    客户端引用被清空(而不是仅把全局变量置 None 了事)。"""
    stopped = []

    class FakeMgr:
        is_ready = True

        async def start(self):
            return None

        async def stop(self):
            stopped.append(True)

    monkeypatch.setattr("app.main.McpClientManager", lambda *a, **k: FakeMgr())

    async def failing_recover():
        raise RuntimeError("recover 注入失败")

    monkeypatch.setattr(runner, "recover_pending_runs", failing_recover)

    import app.main as main_mod
    from app.mcp import client as mcp_client_mod
    with pytest.raises(RuntimeError):
        with TestClient(fastapi_app):
            pass
    assert stopped == [True]                 # stop 确实被调用
    assert main_mod.mcp_manager is None
    assert mcp_client_mod._client is None    # 客户端引用已清空


def test_lifespan_stops_mcp_when_start_fails(monkeypatch):
    """MCP 启动失败同样走清理边界:半启动的 Manager 必须被 stop(),引用清空。"""
    stopped = []

    class FailingMgr:
        is_ready = False

        async def start(self):
            raise RuntimeError("MCP 契约校验失败")

        async def stop(self):
            stopped.append(True)

    monkeypatch.setattr("app.main.McpClientManager", lambda *a, **k: FailingMgr())

    import app.main as main_mod
    from app.mcp import client as mcp_client_mod
    with pytest.raises(RuntimeError):
        with TestClient(fastapi_app):
            pass
    assert stopped == [True]
    assert main_mod.mcp_manager is None
    assert mcp_client_mod._client is None


# ---------- 2) touched_firing 不重复关联 ----------

def test_touched_firing_updates_existing_link_only():
    """同 fingerprint+startsAt 非重复 FIRING(annotations 改变):只更新已有
    Incident,incident_alert 仍一条,Run 不重复,occurrence 按矩阵 +1。"""
    _purge_demo_group()
    fp = uuid.uuid4().hex[:12]
    _process(_payload(fp=fp))                                   # 首次:建 Incident+Run
    inc = _incident_for(fp)
    assert inc.occurrence_count == 1
    out = _process(_payload(fp=fp, annotations={"summary": "renotify"}))  # 非重复 FIRING
    assert out["events"]["appended"] == 1 and out["ignored"] == 0
    inc = _incident_for(fp)
    assert inc.occurrence_count == 2                            # touched_firing +1
    assert len(_links_for(inc.id)) == 1                         # 关联仍一条
    runs = _runs_for(inc.id)
    assert len(runs) == 1                                       # Run 不重复
    assert inc.alert_status == "FIRING"
    _purge_demo_group()


# ---------- 3) 映射失败:不得改写聚合 Incident(FIRING/RESOLVED 两条路径) ----------

def test_mapping_failure_firing_does_not_touch_incident():
    """映射失败的 FIRING:事件+实例照常,但不聚合/不建 Incident/不建 Run。"""
    _purge_demo_group()
    fp = uuid.uuid4().hex[:12]
    out = _process(_payload(fp=fp, service="unknown-service",
                            operation="UNKNOWN_OP"))
    assert out["ignored"] == 1 and out["reasons"] == ["incomplete_labels"]
    assert out["events"]["appended"] == 1
    assert out["created_incidents"] == [] and out["updated_incidents"] == []
    assert _incident_for(fp) is None                    # 未建 Incident
    assert _instance_status(fp) == "FIRING"             # 实例照常投影


def test_mapping_failure_resolved_does_not_touch_incident():
    """映射失败的 RESOLVED 不得改写既有 Incident 聚合。

    先用合法标签建 FIRING(建 Incident + 关联 + queued Run),再用同 fingerprint、
    同 startsAt、但 service/operation 不符合服务端映射的 RESOLVED:请求按
    incomplete_labels 被 ignored,Incident 的 alert_status/occurrence_count/关联/Run
    均不被聚合逻辑改写(仅 AlertEvent/AlertInstance 归档投影)。
    """
    _purge_demo_group()
    fp = uuid.uuid4().hex[:12]
    starts = "2026-09-06T05:00:00.000Z"
    _process(_payload(fp=fp, starts_at=starts))         # 合法 FIRING:建 Incident
    inc = _incident_for(fp)
    assert inc.occurrence_count == 1 and inc.alert_status == "FIRING"
    links_before = [tuple(r) for r in _links_for(inc.id)]
    runs_before = [tuple(r) for r in _runs_for(inc.id)]
    assert len(links_before) == 1 and len(runs_before) == 1

    out = _process(_payload(fp=fp, starts_at=starts, status="resolved",
                            ends_at="2026-09-06T06:00:00.000Z",
                            service="unknown-service", operation="UNKNOWN_OP"))
    assert out["ignored"] == 1 and out["reasons"] == ["incomplete_labels"]
    assert out["events"]["appended"] == 1                # 事件照常归档
    assert out["created_incidents"] == [] and out["updated_incidents"] == []

    inc_after = _incident_for(fp)
    assert inc_after.alert_status == "FIRING"            # 未被误改为 RESOLVED
    assert inc_after.occurrence_count == 1               # 未计数
    assert inc_after.lifecycle_status == "OPEN"
    assert [tuple(r) for r in _links_for(inc_after.id)] == links_before
    assert [tuple(r) for r in _runs_for(inc_after.id)] == runs_before
    assert _instance_status(fp) == "RESOLVED"            # 实例单向投影照常
    _purge_demo_group()


# ---------- 4) 共享 Incident 并发聚合(真实线程并发) ----------


def _concurrent_deliveries(payloads, workers):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_process, payloads))


def test_concurrent_same_group_ten_fingerprints():
    """同 group 10 个不同 fingerprint 并发 FIRING:1 Incident、1 queued Run、
    10 条关联、occurrence=10(真实线程并发,非顺序循环)。"""
    _purge_demo_group()
    fps = [uuid.uuid4().hex[:12] for _ in range(10)]
    payloads = [_payload(fp=fp, starts_at=f"2026-09-06T05:{i:02d}:00.000Z")
                for i, fp in enumerate(fps)]
    outs = _concurrent_deliveries(payloads, workers=10)
    assert all(o["received"] == 1 for o in outs)
    created = [i for o in outs for i in o["created_incidents"]]
    assert len(set(created)) == 1                               # 单一 Incident
    inc_id = created[0]
    with Session(get_control_engine()) as s:
        row = s.execute(text(
            "SELECT occurrence_count, alert_status, lifecycle_status FROM incident "
            "WHERE id=:i"), {"i": inc_id}).fetchone()
        n_links = s.execute(text("SELECT COUNT(*) FROM incident_alert "
                                 "WHERE incident_id=:i"), {"i": inc_id}).scalar()
        n_runs = s.execute(text("SELECT COUNT(*) FROM agent_run WHERE incident_id=:i"),
                           {"i": inc_id}).scalar()
    assert row.occurrence_count == 10
    assert row.alert_status == "FIRING" and row.lifecycle_status == "OPEN"
    assert n_links == 10 and n_runs == 1
    _purge_demo_group()


def test_concurrent_resolved_stable_final_state():
    """同一 Incident 两个 FIRING 实例并发 RESOLVED:终态稳定 RESOLVED。"""
    _purge_demo_group()
    fps = [uuid.uuid4().hex[:12] for _ in range(2)]
    payloads_f = [_payload(fp=f, starts_at=f"2026-09-06T05:{i:02d}:00.000Z")
                  for i, f in enumerate(fps)]
    _concurrent_deliveries(payloads_f, workers=2)
    payloads_r = [_payload(fp=f, starts_at=f"2026-09-06T05:{i:02d}:00.000Z",
                           status="resolved",
                           ends_at=f"2026-09-06T06:{i:02d}:00.000Z")
                  for i, f in enumerate(fps)]
    outs = _concurrent_deliveries(payloads_r, workers=2)
    with Session(get_control_engine()) as s:
        st = s.execute(text(
            "SELECT i.alert_status FROM incident i "
            "JOIN incident_alert ia ON ia.incident_id = i.id "
            "JOIN alert_instance ai ON ai.alert_instance_key = ia.alert_instance_key "
            "WHERE ai.external_fingerprint = :f"), {"f": fps[0]}).scalar()
    assert st == "RESOLVED"
    _purge_demo_group()


def test_concurrent_linked_firing_no_lost_occurrence():
    """已关联实例并发 FIRING(annotations 各异 → 非重复):occurrence 不丢计数。"""
    _purge_demo_group()
    fp = uuid.uuid4().hex[:12]
    _process(_payload(fp=fp))                                   # 首次:建 Incident+关联
    # 5 个并发 FIRING 使用同一 instance(fp+startsAt 相同,annotations 各异 → 非重复):
    # 全部走 touched_firing 路径,occurrence 应 +5(并发下无丢失)
    payloads = [_payload(fp=fp, annotations={"summary": f"r{i}"},
                         starts_at="2026-09-06T05:00:00.000Z")
                for i in range(5)]
    outs = _concurrent_deliveries(payloads, workers=5)
    inc = _incident_for(fp)
    assert inc.occurrence_count == 1 + 5                        # 首次 + 5 并发,无丢失
    _purge_demo_group()


# ---------- 5) DISPATCHED 后启动失败 → 回退 queued 安全重试 ----------

@pytest.mark.asyncio
async def test_start_failure_reverts_to_queued(monkeypatch):
    """mark_dispatched 后 start_investigation 异常 → 回退 queued/READY 清租约,
    下一轮重新领取(dispatch_attempts=2),不永久卡在 investigating/DISPATCHED。"""
    _purge_demo_group()
    inc_id, run_id = _mk_queued()

    # 真实路径:dispatch_once 内 start_investigation 抛异常 → 自动回退 queued
    async def failing_start(incident_id, run_id_, thread_id):
        raise RuntimeError("启动失败注入")

    monkeypatch.setattr(runner, "start_investigation", failing_start)
    await dispatcher.dispatch_once()
    row = _run_row(run_id)
    assert row.status == "queued" and row.dispatch_status == "READY"
    assert row.lease_owner is None and row.lease_until is None

    # 恢复正常后下一轮重新领取并启动(attempts=2)
    started = []

    async def ok_start(incident_id, run_id_, thread_id):
        started.append(run_id_)
        return None

    monkeypatch.setattr(runner, "start_investigation", ok_start)
    await dispatcher.dispatch_once()
    assert started == [run_id]
    assert _run_row(run_id).status == "investigating"
    assert _run_row(run_id).dispatch_attempts == 2


# ---------- 6) 图执行门:recover 串行 + resume 期间 Dispatcher 不启动第二图 ----------

@pytest.mark.asyncio
async def test_recover_pending_runs_not_parallel(monkeypatch, tmp_path):
    """多个 pending Run 重启恢复:图执行经统一门串行(观测最大并发=1)。"""
    runner._saver = None
    monkeypatch.setattr(runner.settings, "checkpoint_path", str(tmp_path / "cp.sqlite"))
    monkeypatch.setattr(runner.settings, "dispatch_enabled", False)

    gate_state = {"cur": 0, "max": 0}
    release = threading.Event()

    class FakeGraph:
        def invoke(self, initial, config):
            gate_state["cur"] += 1
            gate_state["max"] = max(gate_state["max"], gate_state["cur"])
            release.wait(timeout=10)
            gate_state["cur"] -= 1
            return {"status": "needs_human", "termination_reason": "test"}

    monkeypatch.setattr("app.agent.graph.build_graph", lambda **kw: FakeGraph())
    monkeypatch.setattr("app.agent.nodes._emit_status", lambda state: None)

    inc_ids, run_ids = [], []
    for i in range(2):
        with Session(get_control_engine()) as s:
            inc = Incident(title=f"rec-{i}", severity="high",
                           service_ref="inventory-service",
                           affected_operation_ref="INVENTORY_LOOKUP")
            s.add(inc)
            s.commit()
            s.refresh(inc)
            inc_ids.append(inc.id)
        run = run_repo.create_run(inc.id)
        run_repo.update_run_status(run.id, "investigating")
        run_ids.append(run.id)

    await runner.recover_pending_runs()
    await asyncio.sleep(1.0)            # 第二个图应等在执行门外
    assert gate_state["max"] == 1       # 未并行
    assert all(rid in runner._tasks for rid in run_ids)
    release.set()
    for rid in run_ids:
        if rid in runner._tasks:
            await asyncio.wait_for(runner._tasks[rid], timeout=10)
    await asyncio.sleep(0.1)
    assert gate_state["max"] == 1       # 全程未并行


@pytest.mark.asyncio
async def test_resume_holds_gate_dispatcher_blocked(monkeypatch, tmp_path):
    """审批恢复占用执行门期间,Dispatcher 不得启动第二个图(第二个 Run 保持 queued,
    其图任务即便被领取也不执行)。"""
    runner._saver = None
    monkeypatch.setattr(runner.settings, "checkpoint_path", str(tmp_path / "cp.sqlite"))
    monkeypatch.setattr(runner.settings, "dispatch_enabled", True)
    monkeypatch.setattr(runner.settings, "dispatch_interval_seconds", 0.05)

    executed = {"resume": False, "queued": False}
    release = threading.Event()

    class FakeGraph:
        def invoke(self, initial, config=None):
            if isinstance(initial, Command):
                executed["resume"] = True
                release.wait(timeout=15)      # 占住执行门
                return {"status": "needs_human", "termination_reason": "test"}
            executed["queued"] = True          # queued Run 的图执行了 → 违规
            return {"status": "needs_human", "termination_reason": "test"}

    monkeypatch.setattr("app.agent.graph.build_graph", lambda **kw: FakeGraph())

    # run0:awaiting_approval(审批恢复目标)
    with Session(get_control_engine()) as s:
        inc0 = Incident(title="gate-0", severity="high",
                        service_ref="inventory-service",
                        affected_operation_ref="INVENTORY_LOOKUP")
        s.add(inc0)
        s.commit()
        s.refresh(inc0)
    run0 = run_repo.create_run(inc0.id)
    run_repo.update_run_status(run0.id, "awaiting_approval")

    # run2:queued(等待调度)
    _drain_stale_queued()
    inc2_id, run2 = _mk_queued(drain=False)

    resume_task = asyncio.create_task(runner.resume_investigation(
        run0.thread_id, {"decision": "approved"}))
    for _ in range(50):                          # 轮询等待 resume 进入图(最多 5s)
        if executed["resume"]:
            break
        await asyncio.sleep(0.1)
    assert executed["resume"] is True            # resume 已占住执行门
    # 审批恢复期间启动 Dispatcher:第二个图不得执行(门被 resume 占用)
    task = asyncio.create_task(dispatcher.dispatch_loop())
    await asyncio.sleep(0.5)
    assert executed["queued"] is False

    release.set()
    await asyncio.wait_for(resume_task, timeout=15)
    for _ in range(50):                          # 门释放后 queued 图才执行
        if executed["queued"]:
            break
        await asyncio.sleep(0.1)
    assert executed["queued"] is True
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---------- 7) 并发上限唯一来源:按实际 Saver 类型(不看路径扩展名) ----------

def test_concurrency_limit_keys_off_saver_type(monkeypatch, tmp_path):
    """上限由实际 checkpointer 类型决定:.db 后缀 + max_concurrent_runs=4 仍强制 1;
    换成非 SqliteSaver 的共享 checkpointer 时同一点放开。"""
    monkeypatch.setattr(runner, "_saver", None)
    monkeypatch.setattr(runner.settings, "checkpoint_path", str(tmp_path / "cp.db"))
    monkeypatch.setattr(runner.settings, "max_concurrent_runs", 4)
    assert runner.max_concurrent_runs() == 1            # 后缀 .db 不再决定行为

    monkeypatch.setattr(runner, "get_saver", lambda: object())
    assert runner.max_concurrent_runs() == 4


@pytest.mark.asyncio
async def test_dispatcher_capacity_uses_unified_limit(monkeypatch, tmp_path):
    """Dispatcher 容量与执行门同源:.db 后缀 + max_concurrent_runs=4 时单轮仍只
    启动 1 个 queued Run(容量=SqliteSaver 强制的 1,而不是配置的 4)。"""
    monkeypatch.setattr(runner, "_saver", None)
    monkeypatch.setattr(runner.settings, "checkpoint_path", str(tmp_path / "cp.db"))
    monkeypatch.setattr(runner.settings, "max_concurrent_runs", 4)

    started = []

    async def recording_start(incident_id, run_id, thread_id):
        started.append(run_id)

    monkeypatch.setattr(runner, "start_investigation", recording_start)
    _drain_stale_queued()
    _mk_queued(drain=False)
    _mk_queued(drain=False)
    await dispatcher.dispatch_once()
    assert len(started) == 1


# ---------- 8) 图异常(初始化/执行)不得把 Run/Incident 永久留在进行中 ----------

def _incident_row(inc_id):
    with Session(get_control_engine()) as s:
        return s.execute(text(
            "SELECT status, termination_reason FROM incident WHERE id=:i"),
            {"i": inc_id}).fetchone()


def _run_state(run_id):
    with Session(get_control_engine()) as s:
        return s.execute(text(
            "SELECT status, lease_owner, active_run_key FROM agent_run WHERE id=:i"),
            {"i": run_id}).fetchone()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,expected_reason,expect_executed", [
    ("init", "GRAPH_INIT_FAILED", False),
    ("execution", "GRAPH_EXECUTION_FAILED", True),
])
async def test_graph_failure_never_leaves_run_in_progress(
        monkeypatch, tmp_path, phase, expected_reason, expect_executed):
    """真实 Dispatcher 路径(mark_dispatched → start_investigation → 后台任务):
    初始化失败与执行失败都落明确终态,Run/Incident 不会永久处于进行中;
    两类失败原因码区分,且都不回退 queued 重跑(执行可能已产生写操作)。"""
    monkeypatch.setattr(runner, "_saver", None)
    monkeypatch.setattr(runner.settings, "checkpoint_path", str(tmp_path / "cp.sqlite"))
    monkeypatch.setattr(runner.settings, "max_concurrent_runs", 1)

    executed = []

    if phase == "init":
        def failing_build(**kwargs):        # 图尚未启动:init 阶段即抛错
            raise RuntimeError("build_graph 注入失败")

        monkeypatch.setattr("app.agent.graph.build_graph", failing_build)
    else:
        class FailingGraph:                 # 执行已开始:invoke 阶段抛错
            def invoke(self, initial, config):
                executed.append(True)
                raise RuntimeError("invoke 注入失败")

        monkeypatch.setattr("app.agent.graph.build_graph", lambda **kw: FailingGraph())

    _drain_stale_queued()
    inc_id, run_id = _mk_queued(drain=False)
    await dispatcher.dispatch_once()
    for _ in range(100):                    # 等待后台任务收尾
        if _run_state(run_id).status == "failed":
            break
        await asyncio.sleep(0.05)

    state = _run_state(run_id)
    assert state.status == "failed"                 # 不是 investigating/queued
    assert state.lease_owner is None and state.active_run_key is None
    inc = _incident_row(inc_id)
    assert inc.status == "failed"                   # 不是 investigating/created
    assert inc.termination_reason == expected_reason
    assert bool(executed) is expect_executed        # 区分"图尚未启动"与"执行已开始"
    assert run_id not in runner._tasks              # 任务已收尾,不占容量
