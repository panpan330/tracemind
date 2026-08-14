"""Run 观测聚合 + 卡点诊断(只读)。"""
from app.repositories.model_call_repo import list_model_calls_by_run
from app.repositories.retrieval_repo import list_retrievals_by_run
from app.repositories.tool_repo import list_tool_calls_by_run

_PHASE_BY_NODE = {
    "hypothesize": "hypothesize",
    "collect_evidence": "collect_evidence",
    "diagnose": "diagnose",
    "fix": "fix",
    "recovery": "recovery",
}


def _run_summary(incident_id: int, run_id: int) -> dict:
    from app.repositories import run_repo, incident_repo
    run = run_repo.get_run(run_id)
    inc = incident_repo.get_incident(incident_id)
    return {"status": getattr(run, "status", "unknown") if run else "unknown",
            "terminationReason": getattr(inc, "termination_reason", None) if inc else None}


def build_run_observation(incident_id: int, run_id: int) -> dict:
    llms = list_model_calls_by_run(run_id)
    tools = list_tool_calls_by_run(run_id)
    retrs = list_retrievals_by_run(run_id)

    timeline = []
    for m in llms:
        attempts = _parse_attempts(m.get("attempts_json") or "")
        timeline.append({
            "type": "llm",
            "phase": _PHASE_BY_NODE.get(m.get("node", ""), m.get("node", "")),
            "startedAt": None,
            "durationMs": m.get("latency_ms") or 0,
            "detail": {
                "node": m.get("node"), "model": m.get("model"),
                "promptVersion": m.get("prompt_version"),
                "inputTokens": m.get("input_tokens"), "outputTokens": m.get("output_tokens"),
                "latencyMs": m.get("latency_ms"), "retries": max(len(attempts) - 1, 0),
                "finishReason": m.get("finish_reason"),
                "structuredOutputValid": bool(m.get("structured_output_valid")),
                "fallbackTriggered": bool(m.get("fallback_executor")),
                "knowledgeChunkIds": [x for x in (m.get("knowledge_chunk_ids") or "").split(",") if x],
            },
        })
    for t in tools:
        timeline.append({
            "type": "tool", "phase": "collect_evidence", "startedAt": None,
            "durationMs": t.get("duration_ms") or 0,
            "detail": {"name": t.get("tool_name"), "transport": t.get("transport"),
                       "outcome": t.get("status"), "latencyMs": t.get("duration_ms")},
        })
    for r in retrs:
        timeline.append({
            "type": "retrieval", "phase": "hypothesize", "startedAt": None,
            "durationMs": r.get("latency_ms") or 0,
            "detail": {"hitDocIds": [x for x in (r.get("final_chunk_ids") or "").split(",") if x],
                       "scores": [float(x) for x in (r.get("scores") or "").split(",") if x],
                       "latencyMs": r.get("latency_ms"), "degraded": bool(r.get("degraded"))},
        })

    summary = _run_summary(incident_id, run_id)
    diagnosis = _diagnose(llms, tools, summary.get("terminationReason"))
    return {"run": {"runId": run_id, **summary},
            "timeline": timeline, "diagnosis": diagnosis}


def _parse_attempts(s: str) -> list:
    import json
    try:
        return json.loads(s or "[]")
    except Exception:  # noqa: BLE001
        return []


def _diagnose(llms: list, tools: list, termination_reason: str | None) -> dict:
    anomalies = []
    seen_tools = {}
    for t in tools:
        name = t.get("tool_name")
        seen_tools[name] = seen_tools.get(name, 0) + 1
        if t.get("status") in ("failed", "error"):
            anomalies.append({"type": "tool_failed", "stepId": None,
                              "detail": f"{name} status={t.get('status')}"})
    for name, n in seen_tools.items():
        if n >= 2:
            anomalies.append({"type": "duplicate_tool_call", "stepId": None,
                              "detail": f"{name} 调用 {n} 次"})
    for m in llms:
        if max(len(_parse_attempts(m.get("attempts_json") or "")) - 1, 0) > 0:
            anomalies.append({"type": "retry", "stepId": None, "detail": m.get("node")})
        if m.get("fallback_executor"):
            anomalies.append({"type": "fallback_triggered", "stepId": None,
                              "detail": m.get("node")})
        if not m.get("structured_output_valid"):
            anomalies.append({"type": "structured_output_invalid", "stepId": None,
                              "detail": m.get("node")})
    phase_ms = {}
    for m in llms:
        phase_ms[m.get("node")] = phase_ms.get(m.get("node"), 0) + (m.get("latency_ms") or 0)
    bottleneck = max(phase_ms, key=phase_ms.get) if phase_ms else None
    if termination_reason == "decision_budget_exhausted":
        anomalies.append({"type": "decision_budget_exhausted", "stepId": None,
                          "detail": "诊断预算耗尽"})
    return {"terminationReason": termination_reason,
            "bottleneckStep": bottleneck, "anomalies": anomalies}
