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
