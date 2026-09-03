"""恢复前冻结版本校验(V2.0-A closure:统一校验器,任一不匹配 fail closed)。"""
import pytest

from app.repositories import run_repo
from app.services import runner


@pytest.mark.asyncio
async def test_resume_skips_when_version_mismatch(monkeypatch):
    """冻结 Policy 版本与当前不一致 → 停止原 Run(version_mismatch),不恢复图。"""
    import uuid

    from sqlalchemy.orm import Session

    from app.db.engine import get_control_engine
    from app.db.models import AgentRun, Incident
    with Session(get_control_engine()) as s:
        inc = Incident(title="vm", description="x", severity="high",
                       service_ref="inventory-service")
        s.add(inc)
        s.commit()
        s.refresh(inc)
        inc_id = inc.id
    run = run_repo.create_run(inc_id)  # 创建事务冻结 1.0
    called = {}

    async def fake_invoke(*a, **k):
        called["invoked"] = True
        return {"status": "executing"}

    monkeypatch.setattr("app.agent.graph.build_graph", lambda **k: type(
        "G", (), {"invoke": fake_invoke})())
    import app.replay.versions as versions
    monkeypatch.setattr(versions, "POLICY_BUNDLE_VERSION", "9.9.9-future")

    # 不存在的 thread:静默拒绝(fail closed,不崩溃)
    await runner.resume_investigation(f"t-vm-missing-{uuid.uuid4().hex[:8]}",
                                      {"decision": "approved"})
    assert "invoked" not in called

    # 真实 thread:版本不匹配 → 图不被调用,Run failed + needs_human(version_mismatch)
    await runner.resume_investigation(run.thread_id, {"decision": "approved"})
    assert "invoked" not in called
    with Session(get_control_engine()) as s:
        r = s.get(AgentRun, run.id)
        assert r.status == "failed"
        inc_row = s.get(Incident, inc_id)
        assert inc_row.status == "needs_human"
        assert inc_row.termination_reason == "version_mismatch"
