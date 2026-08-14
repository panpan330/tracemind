"""L1 锁等待评估:锁场景未达阈值=暂态重采;慢查询场景无锁=确定性否定。"""
from app.agent.nodes import _evaluate_lock_waiters


def _wait(duration_ms):
    return {"object_schema": "tracemind_business", "object_table": "inventory",
            "waiting_query_ref": "INVENTORY_RESERVATION", "wait_duration_ms": duration_ms}


def test_lock_reached_threshold_is_positive():
    ev = _evaluate_lock_waiters({"success": True, "data": {"waits": [_wait(5000)]}},
                                {"affected_operation_ref": "INVENTORY_RESERVATION"})
    assert len(ev) == 1 and ev[0]["passed"] is True


def test_lock_below_threshold_is_transient():
    ev = _evaluate_lock_waiters({"success": True, "data": {"waits": [_wait(1000)]}},
                                {"affected_operation_ref": "INVENTORY_RESERVATION"})
    assert ev == []


def test_lock_absent_is_transient_for_lock_scenario():
    ev = _evaluate_lock_waiters({"success": True, "data": {"waits": []}},
                                {"affected_operation_ref": "INVENTORY_RESERVATION"})
    assert ev == []


def test_lock_absent_is_negative_for_slow_scenario():
    ev = _evaluate_lock_waiters({"success": True, "data": {"waits": []}},
                                {"affected_operation_ref": "INVENTORY_LOOKUP"})
    assert len(ev) == 1 and ev[0]["passed"] is False
