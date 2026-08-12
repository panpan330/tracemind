"""ReplayProjector:阶段记录 → UI 步骤(Attempt 组装 + 时长投影 + 关键节点下标)。
- 按 (logical_step_id) 聚合多 phase 记录为一个 UI 步骤;stepIndex 为投影下标。
- displayDurationMs 由 step_type + actualDurationMs + playbackPolicyVersion 计算(播放策略,不落库)。
- started 无终态 → stepState=incomplete + missingParts;连续性校验:前步 stateAfter ≠ 后步 stateBefore 标记缺失转换。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import IncidentReplayStep
from app.replay.versions import REPLAY_SCHEMA_VERSION, PLAYBACK_POLICY_VERSION

# 每 step_type 默认展示时长(ms);实际按策略投影
_DISPLAY_MS = {
    "INCIDENT_INGESTED": 1500, "HYPOTHESES_GENERATED": 2500,
    "EVIDENCE_COLLECTION": 3000, "DIAGNOSIS_EVALUATED": 2500,
    "FIX_PROPOSED": 2000, "APPROVAL_REQUESTED": 1500,
    "APPROVAL_DECIDED": 2500, "ACTION_REVALIDATED": 2000,
    "FIX_EXECUTED": 2500, "RECOVERY_VERIFIED": 3500,
    "REPORT_GENERATED": 2000, "RUN_TERMINATED": 1000,
}

# 关键节点选择:diagnosis=第一次确认根因;approval=审批决定;execution=最终处置;recovery=最终恢复
_KEY_TYPES = {"diagnosis": "DIAGNOSIS_EVALUATED", "approval": "APPROVAL_DECIDED",
              "execution": "FIX_EXECUTED", "recovery": "RECOVERY_VERIFIED"}


class ReplayProjector:
    def project(self, rows: list, meta: dict | None = None) -> dict:
        """rows: IncidentReplayStep 对象(按 sequence_no 升序)。返回可播放步骤序列。"""
        by_logical: dict[str, list] = {}
        for r in rows:
            by_logical.setdefault(r.logical_step_id, []).append(r)
        ordered = sorted(by_logical.items(),
                         key=lambda kv: min(x.sequence_no for x in kv[1]))
        steps = []
        for idx, (lid, recs) in enumerate(ordered):
            recs.sort(key=lambda x: x.sequence_no)
            started = next((x for x in recs if x.phase == "started"), None)
            terminal = next((x for x in recs if x.phase in ("completed", "failed")), None)
            state_before = started.state_before_json if started else None
            state_after = terminal.state_after_json if terminal else None
            step_state = ("completed" if terminal and terminal.phase == "completed"
                          else "failed" if terminal else "incomplete")
            missing: list[str] = []
            if not terminal:
                missing = ["stateAfter", "operationResult"]
            elif not state_after:
                missing.append("stateAfter")
            src = (started or terminal).source_references_json or {}
            actual = sum(x.actual_duration_ms or 0 for x in recs)
            steps.append({
                "stepIndex": idx,
                "logicalStepId": lid,
                "sourceSequenceNos": [x.sequence_no for x in recs],
                "stepState": step_state,
                "stepOutcome": (terminal.step_outcome if terminal else None),
                "stepType": (terminal or started).step_type,
                "stepTitle": (terminal or started).step_title,
                "stateBefore": state_before,
                "stateAfter": state_after,
                "missingParts": missing,
                "decisionSummary": (started.decision_json or {}),
                "operationSummary": (terminal.operation_json if terminal else {}),
                "sourceReferenceSummary": src,
                "actualDurationMs": actual,
                "displayDurationMs": self._display_ms(actual, (terminal or started).step_type),
            })
        # 连续性校验:前步 stateAfter ≠ 后步 stateBefore → 标记缺失转换
        for i in range(1, len(steps)):
            prev_after = steps[i - 1].get("stateAfter")
            cur_before = steps[i].get("stateBefore")
            if prev_after is not None and cur_before is not None \
                    and prev_after != cur_before:
                steps[i]["transitionMissing"] = True
        key_indexes: dict[str, int] = {}
        for key, stype in _KEY_TYPES.items():
            candidates = [i for i, st in enumerate(steps) if st["stepType"] == stype]
            if not candidates:
                continue
            if key == "diagnosis":
                confirmed = [i for i in candidates
                             if steps[i].get("stepOutcome") == "confirmed"]
                key_indexes[key] = confirmed[0] if confirmed else candidates[0]
            elif key == "approval":
                key_indexes[key] = candidates[0]
            else:  # execution / recovery:取最终
                key_indexes[key] = candidates[-1]
        return {"totalSteps": len(steps), "keyStepIndexes": key_indexes,
                "steps": steps, "replaySchemaVersion": REPLAY_SCHEMA_VERSION,
                "playbackPolicyVersion": PLAYBACK_POLICY_VERSION}

    @staticmethod
    def _display_ms(actual: int, step_type: str) -> int:
        base = _DISPLAY_MS.get(step_type, 2000)
        if actual <= 0:
            return base
        # 压缩:1s 内用 base;否则线性但封顶 base*2、下限 base//2
        return max(base // 2, min(base * 2, int(actual / 1000) * 500 + base))
