"""Replay API:只读、按 Run 限定、归属校验、后端脱敏。
- GET /api/incidents/{incident_id}/replay                    Incident 级 Manifest + run 列表 + defaultRunId
- GET /api/incidents/{incident_id}/replay/runs/{run_id}       单 Run Manifest
- GET /api/incidents/{incident_id}/replay/runs/{run_id}/steps 单 Run 可播放步骤(一次返回播放必需数据)
- GET /api/incidents/{incident_id}/replay/runs/{run_id}/steps/{logical_step_id} 单步技术详情(懒加载,稳定 ID)"""
from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun, IncidentReplayStep
from app.replay.integrity import check_replay_status
from app.replay.projector import ReplayProjector

router = APIRouter(prefix="/api/incidents/{incident_id}/replay", tags=["replay"])
_projector = ReplayProjector()


def _get_run_or_404(incident_id: int, agent_run_id: int) -> AgentRun:
    with Session(get_control_engine()) as s:
        run = s.get(AgentRun, agent_run_id)
        if run is None or run.incident_id != incident_id:
            raise HTTPException(404, "run not found for incident")
        return run


def _as_of_sequence_no(agent_run_id: int) -> int:
    with Session(get_control_engine()) as s:
        mx = s.scalar(select(func.max(IncidentReplayStep.sequence_no)).where(
            IncidentReplayStep.agent_run_id == agent_run_id))
        return mx or 0


@router.get("")
def incident_manifest(incident_id: int) -> dict:
    with Session(get_control_engine()) as s:
        runs = s.scalars(select(AgentRun).where(
            AgentRun.incident_id == incident_id).order_by(AgentRun.id.asc())).all()
        # defaultRunId:优先最新已终止 Run(terminated_at DESC, id DESC);无则最新 in_progress;全无 null
        terminated = [r for r in runs if r.finished_at is not None]
        default_run_id = None
        if terminated:
            default_run_id = max(terminated, key=lambda r: (r.finished_at, r.id)).id
        elif runs:
            default_run_id = runs[-1].id
    return {"incidentId": incident_id,
            "runs": [{"agentRunId": r.id, "status": r.status,
                      "finishedAt": str(r.finished_at) if r.finished_at else None}
                     for r in runs],
            "defaultRunId": default_run_id,
            "responseSchemaVersion": "1.0"}


@router.get("/runs/{agent_run_id}")
def run_manifest(incident_id: int, agent_run_id: int) -> dict:
    run = _get_run_or_404(incident_id, agent_run_id)
    status = check_replay_status(agent_run_id)
    return {"agentRunId": agent_run_id, **status,
            "asOfSequenceNo": _as_of_sequence_no(agent_run_id),
            "sourceReplaySchemaVersion": "1.0",
            "responseSchemaVersion": "1.0",
            "playbackPolicyVersion": "1",
            "supportedSpeeds": [1, 2, 4],
            "totalSteps": None, "keyStepIndexes": None}


@router.get("/runs/{agent_run_id}/steps")
def run_steps(incident_id: int, agent_run_id: int) -> dict:
    _get_run_or_404(incident_id, agent_run_id)
    with Session(get_control_engine()) as s:
        rows = list(s.scalars(select(IncidentReplayStep).where(
            IncidentReplayStep.agent_run_id == agent_run_id).order_by(
            IncidentReplayStep.sequence_no.asc())).all())
    status = check_replay_status(agent_run_id)
    projected = _projector.project(rows)
    return {**status, **projected}


@router.get("/runs/{agent_run_id}/steps/{logical_step_id}")
def step_detail(incident_id: int, agent_run_id: int, logical_step_id: str) -> dict:
    _get_run_or_404(incident_id, agent_run_id)
    with Session(get_control_engine()) as s:
        rows = list(s.scalars(select(IncidentReplayStep).where(
            IncidentReplayStep.agent_run_id == agent_run_id,
            IncidentReplayStep.logical_step_id == logical_step_id).order_by(
            IncidentReplayStep.sequence_no.asc())).all())
    if not rows:
        raise HTTPException(404, "step not found")
    terminal = next((x for x in rows if x.phase in ("completed", "failed")), rows[0])
    return {"logicalStepId": logical_step_id,
            "decision": terminal.decision_json or (rows[0].decision_json or {}),
            "operation": terminal.operation_json or {},
            "sourceReferences": terminal.source_references_json or {},
            "versions": {"policyBundle": terminal.policy_bundle_version,
                         "prompt": terminal.prompt_version,
                         "toolContract": terminal.tool_contract_version,
                         "normalizer": terminal.normalization_rule_version,
                         "replaySchema": terminal.replay_schema_version},
            "snapshotHash": terminal.snapshot_hash}
