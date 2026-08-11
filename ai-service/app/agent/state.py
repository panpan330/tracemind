from typing import Annotated, TypedDict


def dedup_by_id(existing: list[dict], updates: list[dict]) -> list[dict]:
    seen = {item["id"] for item in existing}
    merged = list(existing)
    for item in updates:
        if item["id"] not in seen:
            merged.append(item)
            seen.add(item["id"])
    return merged


def append_records(existing: list[dict] | None, updates: list[dict] | None) -> list[dict]:
    """工具调用记录追加(不按 id 去重,保留每次调用)。"""
    return (list(existing) if existing else []) + (list(updates) if updates else [])


class IncidentState(TypedDict, total=False):
    incident_id: int
    run_id: int
    thread_id: str
    severity: str
    service_ref: str
    status: str
    investigation_round: int
    max_investigation_rounds: int
    tool_call_count: int
    max_tool_calls: int
    evidence_gate: dict  # E1~E5 布尔判定(collect_evidence 产出);V1.3 含 L1/L2
    termination_reason: str | None
    hypotheses: Annotated[list[dict], dedup_by_id]
    evidence: Annotated[list[dict], dedup_by_id]
    # V1.3 双 Policy 与共享 Fact
    policy: dict          # {"scn001": "confirmed|refuted|unknown|stale", "scn002": ...}
    facts: dict           # 共享 Fact 布尔(见 facts.evaluate_facts)
    root_cause_code: str | None
    lock_evidence_refresh_count: int   # stale 后重采锁关系次数(≤ MAX_LOCK_EVIDENCE_REFRESH)
    # V1.1 降级属性:degraded 是属性不是主状态
    degraded: bool
    degradation_reasons: list[str]
    # V1.1 混合循环预算与记录
    decision_attempt_count: int
    tool_execution_count: int
    consecutive_invalid_count: int
    consecutive_no_progress_count: int
    investigation_started_at: float
    tool_calls_record: Annotated[list[dict], append_records]
    confirmed_hypothesis_id: int | None
    fix_proposal: dict | None
    approval: dict | None
    fix_execution: dict | None
    recovery: dict | None
    report: dict | None
    error: str | None
