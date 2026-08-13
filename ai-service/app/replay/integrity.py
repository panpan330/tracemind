"""回放完整性检查:complete/partial/in_progress/unsupported/unavailable + runOutcome。
语义边界:Run 未结束时孤立的 started 属 in_progress;Run 结束后仍无终态才判 partial。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun, IncidentReplayStep

TERMINAL_RUN_STATUSES = ("completed", "failed", "cancelled", "recovered",
                         "needs_human", "rejected")

# run.status → runOutcome(spec:recovered|failed|rejected|needs_human)
RUN_OUTCOME_MAP = {"recovered": "recovered", "failed": "failed",
                   "rejected": "rejected", "needs_human": "needs_human"}


def check_replay_status(agent_run_id: int) -> dict:
    with Session(get_control_engine()) as s:
        run = s.get(AgentRun, agent_run_id)
        if run is None:
            return {"replayStatus": "unavailable", "runStatus": "unknown",
                    "runOutcome": None, "terminationReason": None,
                    "incompleteSteps": []}
        rows = s.scalars(select(IncidentReplayStep).where(
            IncidentReplayStep.agent_run_id == agent_run_id)).all()
    phases_by_logical: dict[str, set[str]] = {}
    for r in rows:
        phases_by_logical.setdefault(r.logical_step_id, set()).add(r.phase)
    # terminationReason 存于 RUN_TERMINATED 步骤的 decision_json(非 AgentRun 字段)
    term_reason = None
    for r in rows:
        if r.step_type == "RUN_TERMINATED" and r.decision_json:
            term_reason = r.decision_json.get("terminationReason")
            break
    return _evaluate(phases_by_logical, run.status, run.finished_at is not None,
                     termination_reason=term_reason)


def _evaluate(phases_by_logical: dict[str, set[str]], run_status: str,
              run_terminated: bool, termination_reason: str | None = None) -> dict:
    incomplete = [lid for lid, phases in phases_by_logical.items()
                  if "started" in phases and not ({"completed", "failed"} & phases)]
    terminated = run_terminated or run_status in TERMINAL_RUN_STATUSES
    # 未终止的 Run 无调查结果;已终止的按状态映射(completed/cancelled 不在枚举内 → None)
    run_outcome = RUN_OUTCOME_MAP.get(run_status) if terminated else None
    base = {"runOutcome": run_outcome,
            "terminationReason": termination_reason if terminated else None}
    if not terminated:
        return {"replayStatus": "in_progress", "runStatus": run_status,
                "incompleteSteps": [], **base}
    if incomplete:
        return {"replayStatus": "partial", "runStatus": "terminated",
                "incompleteSteps": incomplete, **base}
    return {"replayStatus": "complete", "runStatus": "terminated",
            "incompleteSteps": [], **base}
