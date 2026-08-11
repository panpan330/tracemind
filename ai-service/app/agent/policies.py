"""双 DiagnosticPolicy:SCN-001(缺索引)/SCN-002(锁阻塞)共享 Fact 引用(设计 V1.3 §4.2~4.4)。
状态枚举:confirmed / refuted / unknown / stale(禁用 not_confirmed)。"""

ROOT_CAUSE_INDEX = "MISSING_INVENTORY_INDEX"
ROOT_CAUSE_LOCK = "LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION"

POLICY_SCN001 = ("F_ENDPOINT_DEGRADED", "F_DB_STAGE_DOMINANT",
                 "F_TARGET_QUERY_EXPENSIVE", "F_PLAN_FULL_SCAN", "F_INDEX_MISSING")
POLICY_SCN002 = ("F_ENDPOINT_DEGRADED", "F_DB_STAGE_DOMINANT",
                 "F_TARGET_LOCK_WAIT", "F_BLOCKER_CONFIRMED", "F_BLOCKER_LONG_RUNNING")


def evaluate_policies(facts_dict: dict[str, bool]) -> dict[str, str]:
    """优先级:任一必需 Fact 已知且为 False → refuted;全部已知且全 True → confirmed;
    否则(存在未采集键且已知键无 False)→ unknown。"""
    out: dict[str, str] = {}
    for name, keys in (("scn001", POLICY_SCN001), ("scn002", POLICY_SCN002)):
        known = [k for k in keys if k in facts_dict]
        if any(facts_dict.get(k) is False for k in known):
            out[name] = "refuted"
        elif len(known) == len(keys) and all(facts_dict[k] for k in keys):
            out[name] = "confirmed"
        else:
            out[name] = "unknown"
    return out


def evaluate_exclusions(facts_dict: dict[str, bool]) -> dict[str, bool]:
    """自动处置排他条件(非正向证据,设计 §4.3)。
    索引 Fact 未知时视为 False(不允许自动终止)。
    无锁阻塞判定:F_TARGET_LOCK_WAIT 已采集且为 False(未采集=None → False,不允许自动处置)。"""
    idx_ok = (facts_dict.get("F_INDEX_MISSING") is False
              and facts_dict.get("F_PLAN_FULL_SCAN") is False)
    lock_absent = facts_dict.get("F_TARGET_LOCK_WAIT") is False
    return {"x_index_normal": bool(idx_ok), "x_no_target_lock_wait": bool(lock_absent)}


def decide_root_cause(pol: dict[str, str], exclusions: dict[str, bool]) -> tuple[str | None, str | None]:
    """四分支判定(设计 §4.4)。返回 (root_cause | None, termination_reason | None)。"""
    s1, s2 = pol.get("scn001"), pol.get("scn002")
    if s1 == "confirmed" and s2 == "confirmed":
        return None, "multiple_confirmed_causes"
    if s1 == "confirmed" and s2 == "refuted" and exclusions.get("x_no_target_lock_wait"):
        return ROOT_CAUSE_INDEX, None
    if s2 == "confirmed" and s1 == "refuted" and exclusions.get("x_index_normal"):
        return ROOT_CAUSE_LOCK, None
    # 任一 confirmed 且竞争 unknown/stale,或两者 refuted → 继续收集(不在此处结束)
    return None, None
