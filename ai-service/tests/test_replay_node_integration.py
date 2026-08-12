import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun, IncidentReplayStep
from app.agent import nodes
from app.replay.writer import ReplayWriter


@pytest.fixture()
def run_id() -> int:
    with Session(get_control_engine()) as s:
        r = AgentRun(incident_id=999004, thread_id=f"t-ni-{uuid.uuid4().hex[:8]}",
                     status="created")
        s.add(r)
        s.commit()
        s.refresh(r)
        return r.id


def _base_state(run_id):
    return {"incident_id": 999004, "run_id": run_id, "service_ref": "inventory-service",
            "severity": "high", "status": "investigating",
            "hypotheses": [{"id": "h1", "description": "缺索引", "status": "proposed"}],
            "evidence": [], "evidence_gate": {}, "facts": {}, "policy": {},
            "root_cause_code": None, "confirmed_hypothesis_id": None,
            "termination_reason": None, "max_investigation_rounds": 5, "max_tool_calls": 25}


def test_ingest_writes_step(run_id):
    """ingest 写入 INCIDENT_INGESTED started+completed 两段式。"""
    nodes.ingest(_base_state(run_id))
    with Session(get_control_engine()) as s:
        rows = s.scalars(select(IncidentReplayStep).where(
            IncidentReplayStep.agent_run_id == run_id,
            IncidentReplayStep.step_type == "INCIDENT_INGESTED")).all()
        assert len(rows) == 2
        assert {r.phase for r in rows} == {"started", "completed"}
        assert rows[0].logical_step_id == rows[1].logical_step_id


def test_node_without_run_id_no_side_effect(monkeypatch):
    """无 run_id 的 state(现有测试风格)不写回放、不抛错。"""
    wrote = []
    monkeypatch.setattr("app.agent.nodes.replay_writer_for",
                        lambda iid, rid: wrote.append((iid, rid)))
    out = nodes.ingest({"incident_id": 999004, "status": "created"})
    assert out["status"] == "investigating"
    assert wrote == []  # 无 run_id 跳过
