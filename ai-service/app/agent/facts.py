"""共享 Fact 判定:每次工具返回后由 collect_evidence 重算(设计 V1.3 §4.1)。
输入 evidence dict(key -> {"content": {...}, "passed": bool}),输出 Fact 布尔。"""

# 长事务阈值(ms):阻塞事务年龄超过此值才构成"长事务"
LONG_TRANSACTION_THRESHOLD_MS = 5000
# 锁等待阈值(ms):目标锁等待时长超过此值才构成有效锁等待
LOCK_WAIT_THRESHOLD_MS = 3000

_SCHEMA = "tracemind_business"
_TABLE = "inventory"
_QUERY_REF = "INVENTORY_RESERVATION"


def evaluate_facts(evidence: dict[str, dict]) -> dict[str, bool]:
    """evidence: {key: {"content": ..., "passed": bool}}。
    Fact 真值来源:passed + content 结构判定(与 V1.0 评估器语义一致)。"""
    def content(key: str) -> dict:
        ev = evidence.get(key) or {}
        return ev.get("content") or {}

    # ---- 索引链路 ----
    e5 = content("e5")
    idx = (e5 or {}).get("indexes") or []
    index_missing = bool(e5) and not any(
        i.get("index_name") == "idx_sku_warehouse" for i in idx
    )
    e4 = content("e4")
    plan = ((e4 or {}).get("explain") or {}).get("query_block", {})
    full_scan = (plan.get("table") or {}).get("access_type") == "ALL"
    expensive = bool(content("e3"))  # digest 增量非空即视为高代价 SQL

    # ---- 锁链路(L1/L2 → Fact;复合匹配见 collect_evidence 评估器)----
    l1 = content("l1")
    waits = (l1 or {}).get("waits") or []
    target_waits = [
        w for w in waits
        if w.get("object_schema") == _SCHEMA
        and w.get("object_table") == _TABLE
        and w.get("waiting_query_ref") == _QUERY_REF
    ]
    target_wait = bool(target_waits) and any(
        (w.get("wait_duration_ms") or 0) >= LOCK_WAIT_THRESHOLD_MS
        for w in target_waits
    )
    l2 = content("l2")
    blocker_confirmed = bool(l1 and l2 and l2.get("transaction_id") is not None
                             and bool(target_waits))
    long_running = (l2.get("age_ms") or 0) >= LONG_TRANSACTION_THRESHOLD_MS

    return {
        "F_ENDPOINT_DEGRADED": bool(evidence.get("e1") and (content("e1").get("p95Ms") is not None)),
        "F_DB_STAGE_DOMINANT": bool(evidence.get("e2")),
        "F_TARGET_QUERY_EXPENSIVE": expensive,
        "F_INDEX_MISSING": index_missing,
        "F_PLAN_FULL_SCAN": full_scan,
        "F_TARGET_LOCK_WAIT": target_wait,
        "F_BLOCKER_CONFIRMED": blocker_confirmed,
        "F_BLOCKER_LONG_RUNNING": long_running,
    }
