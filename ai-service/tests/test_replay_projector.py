import uuid

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun, IncidentReplayStep
from app.replay.integrity import check_replay_status, _evaluate
from app.replay.projector import ReplayProjector


def _step(agent_run_id, logical, phase, seq, step_type="EVIDENCE_COLLECTION",
          outcome=None, before=None, after=None, decision=None, operation=None,
          refs=None, duration=None):
    return IncidentReplayStep(
        incident_id=agent_run_id, agent_run_id=agent_run_id, logical_step_id=logical,
        phase=phase, sequence_no=seq, step_type=step_type, step_outcome=outcome,
        state_before_json=before, state_after_json=after,
        decision_json=decision, operation_json=operation,
        source_references_json=refs, actual_duration_ms=duration,
        replay_schema_version="1.0")


def _run_id() -> int:
    with Session(get_control_engine()) as s:
        r = AgentRun(incident_id=999008, thread_id=f"t-pj-{uuid.uuid4().hex[:8]}",
                     status="completed", finished_at=__import__("datetime").datetime.utcnow())
        s.add(r)
        s.commit()
        s.refresh(r)
        # 清残留
        s.execute(delete(IncidentReplayStep).where(
            IncidentReplayStep.agent_run_id == r.id))
        s.commit()
        return r.id


def test_projector_groups_phases_into_steps():
    run = _run_id()
    rows = [
        _step(run, "a", "started", 1, before={"facts": {}},
              decision={"selectedTool": "get_trace"}, duration=10),
        _step(run, "a", "completed", 2, outcome="succeeded",
              after={"facts": {"F_INDEX_MISSING": True}},
              operation={"toolName": "get_trace"}, duration=5),
        _step(run, "b", "started", 3, step_type="FIX_EXECUTED", before={"facts": {}}),
    ]
    out = ReplayProjector().project(rows, {"replayStatus": "partial"})
    assert out["totalSteps"] == 2
    step_a = out["steps"][0]
    assert step_a["stepIndex"] == 0 and step_a["logicalStepId"] == "a"
    assert step_a["sourceSequenceNos"] == [1, 2]
    assert step_a["stepState"] == "completed" and step_a["stepOutcome"] == "succeeded"
    assert step_a["stateAfter"]["facts"]["F_INDEX_MISSING"] is True
    step_b = out["steps"][1]
    assert step_b["stepState"] == "incomplete"
    assert "stateAfter" in step_b["missingParts"]
    assert step_b["displayDurationMs"] > 0  # 投影层计算


def test_check_replay_status_partial_when_started_without_terminal():
    status = _evaluate({"a": {"started", "completed"}, "b": {"started"}},
                       "completed", True)
    assert status["replayStatus"] == "partial"
    assert "b" in status["incompleteSteps"]


def test_check_replay_status_in_progress_when_run_active():
    status = _evaluate({"a": {"started"}}, "investigating", False)
    assert status["replayStatus"] == "in_progress"


def test_check_replay_status_complete():
    status = _evaluate({"a": {"started", "completed"}}, "recovered", True)
    assert status["replayStatus"] == "complete"


def test_check_replay_status_returns_run_outcome_and_termination_reason():
    """spec:Manifest 必须单独返回 runOutcome/terminationReason,避免'完整记录'被误读为'调查成功'。"""
    # 完整闭环 recovered → runOutcome=recovered
    status = _evaluate({"a": {"started", "completed"}}, "recovered", True,
                       termination_reason=None)
    assert status["runStatus"] == "terminated"
    assert status["runOutcome"] == "recovered"
    assert status["terminationReason"] is None

    # rejected 路径 → runOutcome=rejected
    status = _evaluate({"a": {"started", "completed"}}, "rejected", True,
                       termination_reason="approval_rejected")
    assert status["runOutcome"] == "rejected"
    assert status["terminationReason"] == "approval_rejected"

    # 未终止 Run → runOutcome=None(语义:调查还在进行,无结果)
    status = _evaluate({"a": {"started"}}, "investigating", False)
    assert status["runOutcome"] is None
    assert status["terminationReason"] is None


def test_key_step_indexes_selection():
    run = _run_id()
    rows = [
        _step(run, "d1", "started", 1, step_type="DIAGNOSIS_EVALUATED",
              decision={"selectedTool": "get_trace"}),
        _step(run, "d1", "completed", 2, step_type="DIAGNOSIS_EVALUATED",
              outcome="evaluated"),
        _step(run, "d2", "started", 3, step_type="DIAGNOSIS_EVALUATED"),
        _step(run, "d2", "completed", 4, step_type="DIAGNOSIS_EVALUATED",
              outcome="confirmed"),  # 第一次确认根因
        _step(run, "ap", "started", 5, step_type="APPROVAL_DECIDED"),
        _step(run, "ap", "completed", 6, step_type="APPROVAL_DECIDED", outcome="approved"),
        _step(run, "fx1", "started", 7, step_type="FIX_EXECUTED"),
        _step(run, "fx1", "completed", 8, step_type="FIX_EXECUTED", outcome="succeeded"),
    ]
    out = ReplayProjector().project(rows)
    # 步骤序:d1(idx0) d2(confirmed,idx1) ap(idx2) fx1(idx3)
    assert out["keyStepIndexes"]["diagnosis"] == 1  # 第一次确认根因(d2)
    assert out["keyStepIndexes"]["approval"] == 2
    assert out["keyStepIndexes"]["execution"] == 3
