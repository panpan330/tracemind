import pytest
from app.tools import lock_queries


@pytest.fixture(autouse=True)
def no_fixture():
    lock_queries.set_fixture(None)
    yield
    lock_queries.set_fixture(None)


def test_lock_waiters_shapes():
    lock_queries.set_fixture({"waits": [{
        "wait_ref": "w1", "waiter_ref": "wa1", "blocker_ref": "blk_1",
        "requesting_transaction_id": 100, "blocking_transaction_id": 88,
        "requesting_processlist_id": 101, "blocking_processlist_id": 88,
        "requesting_lock_ref": "lr1", "blocking_lock_ref": "lr2",
        "object_schema": "tracemind_business", "object_table": "inventory",
        "index_name": "idx_sku_warehouse", "lock_type": "RECORD", "lock_mode": "X",
        "wait_duration_ms": 5200, "waiting_query_ref": "INVENTORY_RESERVATION"}]})
    r = lock_queries.get_lock_waiters("tracemind_business", "inventory", 3000)
    assert r["ok"] is True
    waits = r["data"]["waits"]
    assert waits[0]["blocking_transaction_id"] == 88
    assert "observed_at" in r["data"] and "snapshot_expires_at" in r["data"]


def test_transaction_details_shapes():
    lock_queries.set_fixture({"transaction_id": 88, "processlist_id": 88,
                              "account": "app_business", "age_ms": 12000,
                              "statement_digest": "UPDATE inventory ...",
                              "locked_objects": [{"schema": "tracemind_business",
                                                  "table": "inventory",
                                                  "lock_ref": "lr2"}]})
    r = lock_queries.get_transaction_details("blk_1")
    assert r["ok"] is True and r["data"]["transaction_id"] == 88
    assert r["data"]["processlist_id"] == 88


def test_no_fixture_returns_explicit_result():
    # 未注入 fixture → 真实查询:本地 MySQL 在线则成功(明确数据),否则明确失败;
    # 绝不返回 fixture 伪数据,也不静默伪造。
    r = lock_queries.get_lock_waiters("tracemind_business", "inventory", 3000)
    assert isinstance(r.get("ok"), bool)
    assert "data" in r
    if r["ok"]:
        assert "observed_at" in r["data"] and "snapshot_expires_at" in r["data"]
    else:
        assert "error_message" in r
