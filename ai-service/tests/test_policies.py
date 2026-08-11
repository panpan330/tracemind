import pytest
from app.agent import facts, policies


def _facts(**kwargs):
    """构造 Fact 字典;未指定键默认 False(证据缺失 → Fact 不成立)。"""
    base = {
        "F_ENDPOINT_DEGRADED": False, "F_DB_STAGE_DOMINANT": False,
        "F_TARGET_QUERY_EXPENSIVE": False, "F_INDEX_MISSING": False,
        "F_PLAN_FULL_SCAN": False, "F_TARGET_LOCK_WAIT": False,
        "F_BLOCKER_CONFIRMED": False, "F_BLOCKER_LONG_RUNNING": False,
    }
    base.update(kwargs)
    return base


def test_index_confirmed_lock_refuted():
    f = _facts(F_ENDPOINT_DEGRADED=True, F_DB_STAGE_DOMINANT=True,
               F_TARGET_QUERY_EXPENSIVE=True, F_PLAN_FULL_SCAN=True, F_INDEX_MISSING=True)
    pol = policies.evaluate_policies(f)
    assert pol["scn001"] == "confirmed" and pol["scn002"] == "refuted"
    ex = policies.evaluate_exclusions(f)
    assert ex["x_no_target_lock_wait"] is True
    root, reason = policies.decide_root_cause(pol, ex)
    assert root == policies.ROOT_CAUSE_INDEX and reason is None


def test_lock_confirmed_index_refuted():
    f = _facts(F_ENDPOINT_DEGRADED=True, F_DB_STAGE_DOMINANT=True,
               F_TARGET_LOCK_WAIT=True, F_BLOCKER_CONFIRMED=True,
               F_BLOCKER_LONG_RUNNING=True, F_INDEX_MISSING=False,
               F_PLAN_FULL_SCAN=False, F_TARGET_QUERY_EXPENSIVE=False)
    pol = policies.evaluate_policies(f)
    assert pol["scn002"] == "confirmed" and pol["scn001"] == "refuted"
    ex = policies.evaluate_exclusions(f)
    assert ex["x_index_normal"] is True
    root, _ = policies.decide_root_cause(pol, ex)
    assert root == policies.ROOT_CAUSE_LOCK


def test_both_confirmed_goes_needs_human():
    f = _facts(F_ENDPOINT_DEGRADED=True, F_DB_STAGE_DOMINANT=True,
               F_TARGET_QUERY_EXPENSIVE=True, F_PLAN_FULL_SCAN=True, F_INDEX_MISSING=True,
               F_TARGET_LOCK_WAIT=True, F_BLOCKER_CONFIRMED=True, F_BLOCKER_LONG_RUNNING=True)
    pol = policies.evaluate_policies(f)
    root, reason = policies.decide_root_cause(pol, policies.evaluate_exclusions(f))
    assert root is None and reason == "multiple_confirmed_causes"


def test_lock_confirmed_index_unknown_keep_collecting():
    # 索引 Fact 键缺失(未采集)→ scn001 unknown
    f = {"F_ENDPOINT_DEGRADED": True, "F_DB_STAGE_DOMINANT": True,
         "F_TARGET_LOCK_WAIT": True, "F_BLOCKER_CONFIRMED": True,
         "F_BLOCKER_LONG_RUNNING": True}
    pol = policies.evaluate_policies(f)
    assert pol["scn001"] == "unknown"
    root, _ = policies.decide_root_cause(pol, policies.evaluate_exclusions(f))
    assert root is None  # 继续收集


def test_lock_confirmed_index_stale_keep_collecting():
    f = _facts(F_ENDPOINT_DEGRADED=True, F_DB_STAGE_DOMINANT=True,
               F_TARGET_LOCK_WAIT=True, F_BLOCKER_CONFIRMED=True, F_BLOCKER_LONG_RUNNING=True)
    pol = policies.evaluate_policies(f)
    pol["scn001"] = "stale"
    root, _ = policies.decide_root_cause(pol, policies.evaluate_exclusions(f))
    assert root is None


def test_both_refuted_keeps_investigating():
    pol = {"scn001": "refuted", "scn002": "refuted"}
    root, _ = policies.decide_root_cause(pol, {"x_index_normal": True, "x_no_target_lock_wait": True})
    assert root is None


def test_lock_confirmed_but_auto_termination_unsafe():
    f = _facts(F_ENDPOINT_DEGRADED=True, F_DB_STAGE_DOMINANT=True,
               F_TARGET_LOCK_WAIT=True, F_BLOCKER_CONFIRMED=True, F_BLOCKER_LONG_RUNNING=True,
               F_INDEX_MISSING=True, F_PLAN_FULL_SCAN=True)  # 索引异常 → X-INDEX-NORMAL=false
    pol = policies.evaluate_policies(f)
    ex = policies.evaluate_exclusions(f)
    assert ex["x_index_normal"] is False
    root, reason = policies.decide_root_cause(pol, ex)
    # SCN002 confirmed 但自动终止不安全:不自动处置
    assert root is None or reason is not None


def test_facts_from_evidence_shapes():
    """evaluate_facts 从 evidence 内容提取 Fact(锁链与索引链)。"""
    evidence = {
        "e1": {"content": {"p95Ms": 117}, "passed": True},
        "e2": {"content": {"inventory_service": [
            {"stage": "database", "durationMs": 110},
            {"stage": "total", "durationMs": 120}]}, "passed": True},
        "e5": {"content": {"indexes": []}, "passed": True},
        "l1": {"content": {"waits": [{"blocker_ref": "blk_1",
            "object_schema": "tracemind_business", "object_table": "inventory",
            "index_name": "idx_sku_warehouse", "wait_duration_ms": 5200,
            "waiting_query_ref": "INVENTORY_RESERVATION"}]}, "passed": True},
        "l2": {"content": {"transaction_id": 88, "age_ms": 12000}, "passed": True},
    }
    f = facts.evaluate_facts(evidence)
    assert f["F_ENDPOINT_DEGRADED"] is True
    assert f["F_INDEX_MISSING"] is True
    assert f["F_TARGET_LOCK_WAIT"] is True
    assert f["F_BLOCKER_LONG_RUNNING"] is True
