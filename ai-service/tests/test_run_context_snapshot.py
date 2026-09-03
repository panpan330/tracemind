"""V2.0-A:RunContextSnapshot 冻结(创建事务内)、版本冻结前移、恢复只读快照。"""
import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun, Incident
from app.repositories import incident_repo, run_repo
from app.services import runner
from app.services.run_context import (RunContextInvalid, RunContextMissing,
                                      load_snapshot)


def _make_incident(**overrides) -> Incident:
    with Session(get_control_engine()) as s:
        inc = Incident(title="快照测试", description="x", severity="high",
                       service_ref="inventory-service",
                       affected_service_ref="inventory-service",
                       affected_operation_ref="INVENTORY_RESERVATION")
        for k, v in overrides.items():
            setattr(inc, k, v)
        s.add(inc)
        s.commit()
        s.refresh(inc)
        return inc


def test_create_run_freezes_snapshot_and_versions():
    from app.mcp.contract import MCP_TOOL_CONTRACT_VERSION
    from app.replay.versions import (CAPABILITY_BUNDLE_VERSION, POLICY_BUNDLE_VERSION,
                                     PROMPT_BUNDLE_VERSION)
    inc = _make_incident()
    run = run_repo.create_run(inc.id)
    snap = load_snapshot(run)
    # 上下文逐项冻结
    assert snap.incident_id == inc.id and snap.agent_run_id == run.id
    assert snap.service_ref == "inventory-service"
    assert snap.affected_operation_ref == "INVENTORY_RESERVATION"
    assert snap.checkpoint == {"thread_id": run.thread_id, "namespace": ""}
    assert snap.baseline_ref is None
    # 版本在创建事务冻结(第 8 条:前移,运行结束不再首次写入)
    assert run.expected_policy_bundle_version == POLICY_BUNDLE_VERSION
    assert run.capability_bundle_version == CAPABILITY_BUNDLE_VERSION
    assert run.prompt_bundle_version == PROMPT_BUNDLE_VERSION
    assert run.tool_bundle_version == MCP_TOOL_CONTRACT_VERSION
    assert snap.bundle_versions["policy"] == POLICY_BUNDLE_VERSION
    # checkpoint thread 与 Run 一一对应(009 唯一约束)
    assert run.checkpoint_thread_id == run.thread_id


def test_snapshot_frozen_against_incident_update():
    """Incident 行被更新后,旧 Run 恢复仍使用冻结快照(不从 Incident 行重推导)。"""
    inc = _make_incident()
    run = run_repo.create_run(inc.id)
    with Session(get_control_engine()) as s:
        s.execute(text(
            "UPDATE incident SET service_ref='order-service', "
            "affected_operation_ref='ORDER_CREATE' WHERE id=:i"), {"i": inc.id})
        s.commit()
    initial = runner._initial_state_from_run(run)
    assert initial["service_ref"] == "inventory-service"          # 冻结值,非更新值
    assert initial["affected_operation_ref"] == "INVENTORY_RESERVATION"


def test_run_without_service_ref_fails_closed():
    inc = _make_incident(service_ref=None)
    with pytest.raises(ValueError, match="service_ref missing"):
        run_repo.create_run(inc.id)


def test_recover_missing_snapshot_fail_closed(monkeypatch, tmp_path):
    """009 之前创建的旧 Run(无快照):恢复时 fail closed,不猜测、不用默认上下文。"""
    monkeypatch.setattr(runner, "_saver", None)
    monkeypatch.setattr(runner.settings, "checkpoint_path", str(tmp_path / "cp.sqlite"))
    inc = _make_incident()
    with Session(get_control_engine()) as s:
        r = AgentRun(incident_id=inc.id, thread_id=f"t-legacy-{uuid.uuid4().hex[:8]}",
                     status="investigating")  # 无 run_context_snapshot_json
        s.add(r)
        s.commit()
        s.refresh(r)

    async def _no_graph(*a, **kw):
        raise AssertionError("缺快照的 Run 不得启动图")

    monkeypatch.setattr("app.agent.graph.build_graph", _no_graph)
    asyncio.run(runner.recover_pending_runs())
    from app.db.models import Incident as Inc
    with Session(get_control_engine()) as s:
        assert s.get(AgentRun, r.id).status == "failed"
        row = s.get(Inc, inc.id)
        assert row.status == "needs_human"
        # V2.0-A closure:缺失与损坏分别有精确原因码
        assert row.termination_reason == "context_snapshot_missing"
    assert r.id not in runner._tasks


def test_snapshot_corrupt_fails_closed():
    run = run_repo.create_run(_make_incident().id)
    with Session(get_control_engine()) as s:
        r = s.get(AgentRun, run.id)
        r.run_context_snapshot_json = {"schema_version": "1.0"}  # 缺全部必填字段
        s.commit()
        with pytest.raises(RunContextInvalid):
            load_snapshot(r)  # 会话内读取,避免 detached 属性过期


def test_load_snapshot_missing_raises():
    class LegacyRun:
        id = 1
        thread_id = "t-x"
        checkpoint_thread_id = "t-x"
        run_context_snapshot_json = None

    with pytest.raises(RunContextMissing):
        load_snapshot(LegacyRun())


def test_version_freeze_effective_at_resume(monkeypatch):
    """创建时冻结的版本与当前代码不一致 → version_mismatch fail closed(图不被调用)。"""
    inc = _make_incident()
    run = run_repo.create_run(inc.id)
    called = []

    def _no_graph(**kw):
        called.append(1)
        raise AssertionError("版本不匹配不得恢复图")

    monkeypatch.setattr("app.agent.graph.build_graph", _no_graph)
    import app.replay.versions as versions
    monkeypatch.setattr(versions, "POLICY_BUNDLE_VERSION", "9.9.9-future")
    asyncio.run(runner.resume_investigation(run.thread_id, {"decision": "approved"}))
    assert called == []
    with Session(get_control_engine()) as s:
        assert s.get(AgentRun, run.id).status == "failed"
        row = s.get(Incident, inc.id)
        assert row.status == "needs_human"
        assert row.termination_reason == "version_mismatch"
