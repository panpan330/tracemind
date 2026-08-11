"""共享 Fact 判定:每次工具返回后由 collect_evidence 重算(设计 V1.3 §4.1)。
Fact 值直接采用 collect_evidence 评估器的 passed 判定(评估器是证据语义判定的权威,
facts 层只做键映射与组合,避免二次解释导致与评估器不一致)。
未采集的证据不输出 Fact 键(policy 判 unknown);已采集判定不成立 → False(policy 判 refuted)。"""

# 长事务阈值(ms)与锁等待阈值(ms):由评估器使用,此处保留常量引用
LONG_TRANSACTION_THRESHOLD_MS = 5000
LOCK_WAIT_THRESHOLD_MS = 3000

# evidence 键 → Fact 键(一一映射,值为评估器 passed)
_FACT_MAP = {
    "e1": "F_ENDPOINT_DEGRADED",
    "e2": "F_DB_STAGE_DOMINANT",
    "e3": "F_TARGET_QUERY_EXPENSIVE",
    "e4": "F_PLAN_FULL_SCAN",
    "e5": "F_INDEX_MISSING",
    "l1": "F_TARGET_LOCK_WAIT",
    "l2": "F_BLOCKER_LONG_RUNNING",
}


def evaluate_facts(evidence: dict[str, dict]) -> dict[str, bool]:
    """evidence: {key: {"content": ..., "passed": bool}}。
    只对**已采集**的证据输出 Fact 键;值 = 评估器 passed。"""
    out: dict[str, bool] = {}
    for ev_key, fact in _FACT_MAP.items():
        ev = evidence.get(ev_key)
        if ev is not None:
            out[fact] = bool(ev.get("passed"))
    # F_BLOCKER_CONFIRMED:目标锁等待 + 阻塞事务详情复合成立(评估器 L1/L2 各自判定)
    if evidence.get("l1") is not None and evidence.get("l2") is not None:
        out["F_BLOCKER_CONFIRMED"] = bool(evidence["l1"].get("passed")
                                          and evidence["l2"].get("passed"))
    return out
