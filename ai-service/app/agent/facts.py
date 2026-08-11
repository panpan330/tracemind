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
    只对**已采集**的证据输出 Fact 键(未采集的 Fact 不出现 → policy 判 unknown);
    已采集但判定不成立 → 键值为 False(policy 判 refuted)。"""
    def content(key: str) -> dict:
        ev = evidence.get(key) or {}
        return ev.get("content") or {}

    out: dict[str, bool] = {}
    if evidence.get("e1"):
        out["F_ENDPOINT_DEGRADED"] = bool(content("e1").get("p95Ms") is not None)
    if evidence.get("e2"):
        out["F_DB_STAGE_DOMINANT"] = True
    if evidence.get("e3"):
        out["F_TARGET_QUERY_EXPENSIVE"] = bool(content("e3"))  # digest 增量非空即高代价
    if evidence.get("e5"):
        idx = (content("e5") or {}).get("indexes") or []
        # 评估器产出字符串列表(如 ["PRIMARY"] / ["idx_sku_warehouse"])
        out["F_INDEX_MISSING"] = not any(
            (i.get("index_name") if isinstance(i, dict) else i) == "idx_sku_warehouse"
            for i in idx
        )
    if evidence.get("e4"):
        e4c = content("e4") or {}
        plan = e4c.get("explain")
        access_type = None
        if isinstance(plan, dict):
            access_type = ((plan.get("query_block") or {}).get("table") or {}).get("access_type")
        else:
            access_type = e4c.get("access_type")  # 评估器扁平结构 {"access_type": ...}
        out["F_PLAN_FULL_SCAN"] = access_type == "ALL"

    # 锁链路(复合匹配)
    l1 = content("l1")
    if evidence.get("l1"):
        target_waits = [
            w for w in ((l1 or {}).get("waits") or [])
            if w.get("object_schema") == _SCHEMA
            and w.get("object_table") == _TABLE
            and w.get("waiting_query_ref") == _QUERY_REF
        ]
        out["F_TARGET_LOCK_WAIT"] = bool(target_waits) and any(
            (w.get("wait_duration_ms") or 0) >= LOCK_WAIT_THRESHOLD_MS
            for w in target_waits
        )
    l2 = content("l2")
    if evidence.get("l1") and evidence.get("l2"):
        target_waits = [
            w for w in ((l1 or {}).get("waits") or [])
            if w.get("object_schema") == _SCHEMA
            and w.get("object_table") == _TABLE
            and w.get("waiting_query_ref") == _QUERY_REF
        ]
        out["F_BLOCKER_CONFIRMED"] = bool(target_waits and l2.get("transaction_id") is not None)
    if evidence.get("l2"):
        out["F_BLOCKER_LONG_RUNNING"] = (l2.get("age_ms") or 0) >= LONG_TRANSACTION_THRESHOLD_MS
    return out
