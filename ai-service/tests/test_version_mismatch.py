import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun, Incident
from app.services import runner


def _make_run(expected_version: str | None) -> tuple[int, int]:
    with Session(get_control_engine()) as s:
        inc = Incident(title="vm", description="x", severity="high",
                       service_ref="inventory-service", status="awaiting_approval")
        s.add(inc)
        s.commit()
        s.refresh(inc)
        r = AgentRun(incident_id=inc.id, thread_id=f"t-vm-{uuid.uuid4().hex[:8]}",
                     status="investigating", expected_policy_bundle_version=expected_version)
        s.add(r)
        s.commit()
        s.refresh(r)
        return inc.id, r.id


@pytest.mark.asyncio
async def test_resume_skips_when_version_mismatch(monkeypatch):
    """预期版本与当前不一致 → 停止原 Run(version_mismatch),不恢复图。"""
    inc_id, run_id = _make_run(expected_version="9.9.9")  # 与 POLICY_BUNDLE_VERSION=1.0 不一致
    called = {}
    from app.replay.versions import POLICY_BUNDLE_VERSION

    async def fake_invoke(*a, **k):
        called["invoked"] = True
        return {"status": "executing"}

    monkeypatch.setattr("app.agent.graph.build_graph", lambda **k: type(
        "G", (), {"invoke": fake_invoke})())
    await runner.resume_investigation(f"t-vm-does-not-exist", {"decision": "approved"})

    # 用真实 thread 再次验证:版本不匹配时不调用图
    with Session(get_control_engine()) as s:
        r = s.get(AgentRun, run_id)
        thread = r.thread_id
        s.get(Incident, inc_id).status  # noqa
    await runner.resume_investigation(thread, {"decision": "approved"})
    assert "invoked" not in called  # 图未被调用
    with Session(get_control_engine()) as s:
        r = s.get(AgentRun, run_id)
        assert r.status == "failed"
        inc = s.get(Incident, inc_id)
        assert inc.status == "needs_human"
        assert inc.termination_reason == "version_mismatch"
