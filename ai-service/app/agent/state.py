from typing import Annotated, TypedDict


def dedup_by_id(existing: list[dict], updates: list[dict]) -> list[dict]:
    seen = {item["id"] for item in existing}
    merged = list(existing)
    for item in updates:
        if item["id"] not in seen:
            merged.append(item)
            seen.add(item["id"])
    return merged


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
    termination_reason: str | None
    hypotheses: Annotated[list[dict], dedup_by_id]
    evidence: Annotated[list[dict], dedup_by_id]
    confirmed_hypothesis_id: int | None
    fix_proposal: dict | None
    approval: dict | None
    fix_execution: dict | None
    recovery: dict | None
    report: dict | None
    error: str | None
