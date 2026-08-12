"""回放完整性检查:complete/partial/in_progress/unsupported/unavailable + runOutcome。
语义边界:Run 未结束时孤立的 started 属 in_progress;Run 结束后仍无终态才判 partial。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun, IncidentReplayStep

TERMINAL_RUN_STATUSES = ("completed", "failed", "cancelled", "recovered",
                         "needs_human", "rejected")


def check_replay_status(agent_run_id: int) -> dict:
    with Session(get_control_engine()) as s:
        run = s.get(AgentRun, agent_run_id)
        if run is None:
            return {"replayStatus": "unavailable", "runStatus": "unknown",
                    "incompleteSteps": []}
        rows = s.scalars(select(IncidentReplayStep).where(
            IncidentReplayStep.agent_run_id == agent_run_id)).all()
    phases_by_logical: dict[str, set[str]] = {}
    for r in rows:
        phases_by_logical.setdefault(r.logical_step_id, set()).add(r.phase)
    return _evaluate(phases_by_logical, run.status, run.finished_at is not None)


def _evaluate(phases_by_logical: dict[str, set[str]], run_status: str,
              run_terminated: bool) -> dict:
    incomplete = [lid for lid, phases in phases_by_logical.items()
                  if "started" in phases and not ({"completed", "failed"} & phases)]
    terminated = run_terminated or run_status in TERMINAL_RUN_STATUSES
    if not terminated:
        return {"replayStatus": "in_progress", "runStatus": run_status,
                "incompleteSteps": []}
    if incomplete:
        return {"replayStatus": "partial", "runStatus": "terminated",
                "incompleteSteps": incomplete}
    return {"replayStatus": "complete", "runStatus": "terminated",
            "incompleteSteps": []}
