import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.main import app
from app.db.engine import get_control_engine
from app.db.models import AgentRun, IncidentReplayStep
from app.replay.writer import ReplayWriter

client = TestClient(app)


def _make_run_with_steps() -> tuple[int, int]:
    from datetime import datetime
    with Session(get_control_engine()) as s:
        r = AgentRun(incident_id=999007, thread_id=f"t-api-{uuid.uuid4().hex[:8]}",
                     status="completed", finished_at=datetime.utcnow())
        s.add(r)
        s.commit()
        s.refresh(r)
        run_id = r.id
        s.execute(delete(IncidentReplayStep).where(
            IncidentReplayStep.agent_run_id == run_id))
        s.commit()
    w = ReplayWriter(999007, run_id)
    w.write("INCIDENT_INGESTED", "started", logical_step_id="ls-a",
            state_before={"facts": {}})
    w.complete("INCIDENT_INGESTED", "ls-a", outcome="succeeded",
               state_after={"facts": {}})
    return 999007, run_id


def test_replay_manifest_and_steps():
    incident_id, run_id = _make_run_with_steps()
    r = client.get(f"/api/incidents/{incident_id}/replay")
    assert r.status_code == 200
    m = r.json()
    assert m["defaultRunId"] == run_id

    r2 = client.get(f"/api/incidents/{incident_id}/replay/runs/{run_id}/steps")
    assert r2.status_code == 200
    body = r2.json()
    assert body["totalSteps"] >= 1
    s0 = body["steps"][0]
    assert "stateBefore" in s0 and "stateAfter" in s0
    assert "displayDurationMs" in s0 and "actualDurationMs" in s0
    assert "keyStepIndexes" in body


def test_replay_run_belongs_to_incident():
    incident_id, run_id = _make_run_with_steps()
    r = client.get(f"/api/incidents/999999/replay/runs/{run_id}/steps")
    assert r.status_code == 404  # 归属校验


def test_replay_is_readonly_no_side_effects():
    incident_id, run_id = _make_run_with_steps()
    r = client.get(f"/api/incidents/{incident_id}/replay/runs/{run_id}/steps")
    assert r.status_code == 200  # 只读,不触发状态机/LLM/MCP


def test_step_detail_lazy_load():
    incident_id, run_id = _make_run_with_steps()
    r = client.get(f"/api/incidents/{incident_id}/replay/runs/{run_id}/steps/ls-a")
    assert r.status_code == 200
    d = r.json()
    assert d["logicalStepId"] == "ls-a"
    assert "versions" in d and "snapshotHash" in d
