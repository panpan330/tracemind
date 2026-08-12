"""ReplayWriter:两段式写入(started → completed/failed),纯追加,logical_step_id 幂等。
序号分配(agent_run.next_replay_sequence 自增)与记录插入在同一数据库事务。"""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import IncidentReplayStep
from app.replay.snapshot import snapshot_hash
from app.replay.versions import REPLAY_SCHEMA_VERSION

PHASES = ("started", "completed", "failed")

# step_type 业务枚举(与 spec §3.4 一致;不用 Python 函数名)
STEP_TYPES = (
    "INCIDENT_INGESTED", "HYPOTHESES_GENERATED", "EVIDENCE_COLLECTION",
    "DIAGNOSIS_EVALUATED", "FIX_PROPOSED", "APPROVAL_REQUESTED",
    "APPROVAL_DECIDED", "ACTION_REVALIDATED", "FIX_EXECUTED",
    "RECOVERY_VERIFIED", "REPORT_GENERATED", "RUN_TERMINATED",
)


class ReplayWriter:
    def __init__(self, incident_id: int, agent_run_id: int):
        self.incident_id = incident_id
        self.agent_run_id = agent_run_id

    def existing_logical_id(self, step_type: str, business_key: str) -> str | None:
        """按业务键幂等查找已有 logical_step_id(审批重试/重复提交复用)。"""
        with Session(get_control_engine()) as s:
            rows = s.scalars(select(IncidentReplayStep).where(
                IncidentReplayStep.agent_run_id == self.agent_run_id,
                IncidentReplayStep.step_type == step_type)).all()
        for r in rows:
            refs = r.source_references_json or {}
            if refs.get("businessKey") == business_key:
                return r.logical_step_id
        return None

    def write(self, step_type: str, phase: str, *, logical_step_id: str | None = None,
              attempt_no: int = 1, step_title: str | None = None,
              step_outcome: str | None = None, round_no: int | None = None,
              state_before: dict | None = None, state_after: dict | None = None,
              decision: dict | None = None, operation: dict | None = None,
              source_refs: dict | None = None,
              actual_duration_ms: int | None = None) -> IncidentReplayStep:
        if step_type not in STEP_TYPES:
            raise ValueError(f"unknown step_type: {step_type}")
        if phase not in PHASES:
            raise ValueError(f"unknown phase: {phase}")
        lid = logical_step_id or f"step_{uuid.uuid4().hex[:12]}"
        with Session(get_control_engine()) as s:
            from app.repositories import run_repo
            seq = run_repo.allocate_replay_sequence(self.agent_run_id, s)  # 同事务
            size = (len(str(source_refs or {})) + len(str(decision or {}))
                    + len(str(state_before or {})) + len(str(state_after or {})))
            step = IncidentReplayStep(
                incident_id=self.incident_id, agent_run_id=self.agent_run_id,
                logical_step_id=lid, phase=phase, attempt_no=attempt_no,
                step_type=step_type, step_title=step_title, step_outcome=step_outcome,
                round_no=round_no, sequence_no=seq,
                state_before_json=state_before, state_after_json=state_after,
                decision_json=decision, operation_json=operation,
                source_references_json=source_refs,
                actual_duration_ms=actual_duration_ms,
                replay_schema_version=REPLAY_SCHEMA_VERSION,
                snapshot_hash=snapshot_hash(state_before or {}),
                payload_size_bytes=size,
            )
            s.add(step)
            s.commit()
            s.refresh(step)
            return step

    def complete(self, step_type: str, logical_step_id: str, *,
                 state_after: dict | None = None, outcome: str | None = None,
                 operation: dict | None = None, source_refs: dict | None = None,
                 actual_duration_ms: int | None = None) -> IncidentReplayStep:
        return self.write(step_type, "completed", logical_step_id=logical_step_id,
                          step_outcome=outcome, state_after=state_after,
                          operation=operation, source_refs=source_refs,
                          actual_duration_ms=actual_duration_ms)

    def fail(self, step_type: str, logical_step_id: str, *,
             outcome: str | None = None, operation: dict | None = None,
             actual_duration_ms: int | None = None) -> IncidentReplayStep:
        return self.write(step_type, "failed", logical_step_id=logical_step_id,
                          step_outcome=outcome, operation=operation,
                          actual_duration_ms=actual_duration_ms)
