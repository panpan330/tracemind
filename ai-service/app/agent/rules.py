GATE_EVIDENCE = ("E1", "E2", "E3", "E4", "E5")

# 证据键 -> 工具名映射(供 collect_evidence 使用)
EVIDENCE_TOOL_MAP = {
    "E1": "get_service_metrics",
    "E2": "get_trace",
    "E3": "list_expensive_query_digests",
    "E4": "get_query_plan",
    "E5": "get_index_info",
}


def evaluate_evidence_gate(evidence: dict[str, bool]) -> bool:
    """E1~E5 全部满足才确认根因(设计 4.3)。"""
    return all(evidence.get(k) is True for k in GATE_EVIDENCE)


def evaluate_recovery_rule(probes: list[dict], baseline_p95: float | None,
                           threshold_ratio: float = 1.2) -> bool:
    """设计 4.4:至少三批探测,每批独立 P95,全部 <= 基线 x 阈值才算恢复。"""
    if len(probes) < 3 or baseline_p95 is None:
        return False
    return all(
        p.get("p95_ms") is not None and p["p95_ms"] <= baseline_p95 * threshold_ratio
        for p in probes
    )
