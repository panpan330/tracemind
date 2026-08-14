import pytest
from app.agent import fix_registry, policies


def test_resolve_lock_action():
    fix = fix_registry.FixRegistry.resolve(policies.ROOT_CAUSE_LOCK)
    assert fix.action_type == "TERMINATE_BLOCKING_SESSION"
    assert fix.risk_level == "high"


def test_build_proposal_lock_uses_evidence():
    state = {
        "incident_id": 5, "run_id": 6, "root_cause_code": policies.ROOT_CAUSE_LOCK,
        "evidence": [{"key": "l1", "content": {"waits": [{
            "blocker_ref": "blk_1", "blocking_transaction_id": 88,
            "blocking_processlist_id": 88, "blocking_lock_ref": "lr2",
            "object_schema": "tracemind_business", "object_table": "inventory",
            "index_name": "idx_sku_warehouse", "requesting_transaction_id": 100,
            "waiting_query_ref": "INVENTORY_RESERVATION"}]}},
            {"key": "l2", "content": {"transaction_id": 88, "processlist_id": 88,
                                      "age_ms": 12000}}],
    }
    prop = fix_registry.build_proposal(state)
    assert prop["action_type"] == "TERMINATE_BLOCKING_SESSION"
    assert prop["parameters"]["processlist_id"] == 88
    assert "blocking_relation_hash" in prop


def test_relation_hash_is_stable_and_excludes_time():
    fields = {"incident_id": 5, "agent_run_id": 6, "blocking_transaction_id": 88,
              "blocking_processlist_id": 88, "blocking_lock_ref": "lr2",
              "waiting_transaction_id": 100, "waiting_query_ref": "INVENTORY_RESERVATION",
              "locked_schema": "tracemind_business", "locked_table": "inventory",
              "locked_index": "idx_sku_warehouse"}
    h1 = fix_registry.build_relation_hash(fields)
    h2 = fix_registry.build_relation_hash(dict(fields))
    assert h1 == h2
    # 时间字段不入 hash
    h3 = fix_registry.build_relation_hash({**fields, "evidence_observed_at": "x"})
    assert h1 == h3


def test_extract_lock_parameters_picks_root_blocker():
    """锁等待链多阻塞者时,应选根阻塞者(只 blocking 不 requesting),而非 target[0](中间层)。"""
    from app.agent.fix_registry import _extract_lock_parameters
    waits = [
        # 中间层:blocking=100 且 requesting=200(既阻塞又被阻塞)
        {"object_schema": "tracemind_business", "object_table": "inventory",
         "waiting_query_ref": "INVENTORY_RESERVATION",
         "blocking_processlist_id": 100, "requesting_processlist_id": 200,
         "blocker_ref": "blk_100", "blocking_transaction_id": "tx-mid",
         "blocking_lock_ref": "blk_100", "requesting_transaction_id": "tx-req",
         "index_name": None},
        # 根阻塞者:blocking=999,只 blocking 不 requesting
        {"object_schema": "tracemind_business", "object_table": "inventory",
         "waiting_query_ref": "INVENTORY_RESERVATION",
         "blocking_processlist_id": 999, "requesting_processlist_id": 100,
         "blocker_ref": "blk_999", "blocking_transaction_id": "tx-root",
         "blocking_lock_ref": "blk_999", "requesting_transaction_id": "tx-mid",
         "index_name": None},
    ]
    state = {"evidence": [
        {"key": "l1", "content": {"waits": waits}},
        {"key": "l2", "content": {"transaction_id": "tx-root", "age_ms": 8000}},
    ]}
    params = _extract_lock_parameters(state)
    assert params["processlist_id"] == 999, f"应选根阻塞者 999,实际 {params['processlist_id']}"
    assert params["blocking_processlist_id"] == 999
