import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun, IncidentReplayStep
from app.agent import nodes


@pytest.fixture()
def run_id() -> int:
    with Session(get_control_engine()) as s:
        r = AgentRun(incident_id=999005, thread_id=f"t-ce-{uuid.uuid4().hex[:8]}",
                     status="created")
        s.add(r)
        s.commit()
        s.refresh(r)
        return r.id


class StubLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def select_tool(self, state, prompt, eligible):
        if self.responses:
            return self.responses.pop(0)
        return [{"name": "get_service_metrics", "arguments": {}}]


def _stub_tools():
    def fake(state, name, args):
        if name == "get_service_metrics":
            return {"ok": True, "evidence": [{"id": "E1", "key": "e1",
                    "source": "get_service_metrics",
                    "content": {"p95Ms": 117, "sourceBackend": "prometheus"},
                    "passed": True}]}
        return {"ok": False, "evidence": []}
    return fake


def _state(run_id):
    return {"incident_id": 999005, "run_id": run_id, "service_ref": "inventory-service",
            "severity": "high", "status": "investigating", "hypotheses": [],
            "evidence": [], "evidence_gate": {}, "facts": {}, "policy": {},
            "max_investigation_rounds": 2, "max_tool_calls": 25,
            "decision_attempt_count": 0, "tool_execution_count": 0,
            "tool_calls_record": [], "consecutive_no_progress_count": 0,
            "consecutive_invalid_count": 0}


def test_collect_evidence_writes_evidence_collection(run_id):
    """collect_evidence 每轮写 EVIDENCE_COLLECTION started+completed。"""
    nodes.collect_evidence(_state(run_id), llm=StubLLM([]), tools=_stub_tools())
    with Session(get_control_engine()) as s:
        rows = s.scalars(select(IncidentReplayStep).where(
            IncidentReplayStep.agent_run_id == run_id,
            IncidentReplayStep.step_type == "EVIDENCE_COLLECTION")).all()
        assert len(rows) >= 2  # started + completed
        rounds = {r.round_no for r in rows if r.round_no is not None}
        assert len(rounds) >= 1
        # started 有 state_before + decision
        started = next(r for r in rows if r.phase == "started")
        assert started.decision_json is not None
        assert "eligibleTools" in started.decision_json
        assert "selectedTool" in started.decision_json
