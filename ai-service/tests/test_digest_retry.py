"""E3 digest 评估:增量全 0 = 暂态(重采);增量>阈值 = 正向;否则确定性否定。"""
from app.capabilities.mysql_missing_index.capability import (
    evaluate_digests as _evaluate_digests)


def test_digest_delta_zero_is_transient():
    r = {"success": True, "data": [{"digest": "SELECT ... FOR SHARE",
                                    "rows_examined_delta": 0}]}
    assert _evaluate_digests(r, {"affected_operation_ref": "INVENTORY_LOOKUP"}) == []


def test_digest_delta_zero_is_negative_for_lock_scenario():
    """锁场景(INVENTORY_RESERVATION)无慢查询增量是确定性否定,产 E3=False 继续锁证据。"""
    r = {"success": True, "data": [{"digest": "SELECT ... FOR SHARE",
                                    "rows_examined_delta": 0}]}
    ev = _evaluate_digests(r, {"affected_operation_ref": "INVENTORY_RESERVATION"})
    assert len(ev) == 1 and ev[0]["passed"] is False


def test_digest_delta_large_is_positive():
    r = {"success": True, "data": [{"digest": "SELECT ... FOR SHARE",
                                    "rows_examined_delta": 14000000}]}
    ev = _evaluate_digests(r, {})
    assert len(ev) == 1 and ev[0]["passed"] is True and ev[0]["id"] == "E3"


def test_digest_small_delta_is_negative():
    r = {"success": True, "data": [{"digest": "SELECT ... FOR SHARE",
                                    "rows_examined_delta": 500}]}
    ev = _evaluate_digests(r, {})
    assert len(ev) == 1 and ev[0]["passed"] is False


def test_digest_lock_wait_latency_signature_is_positive():
    """锁阻塞签名:1205 超时语句 rows_examined=0,但锁等待耗时计入 SUM_TIMER_WAIT
    (live 实测每次 +10s)→ E3=True。只认 rows_examined 会把锁场景判成暂态空增量
    无限重采,直至 decision_budget_exhausted(live 验收缺陷)。"""
    r = {"success": True, "data": [
        {"digest": "SELECT COUNT(*) ...", "rows_examined_delta": 0,
         "total_latency_us_delta": 0},
        {"digest": "SELECT ... FOR SHARE", "rows_examined_delta": 0,
         "total_latency_us_delta": 10_065_768},
    ]}
    ev = _evaluate_digests(r, {"affected_operation_ref": "ORDER_CREATE"})
    assert len(ev) == 1 and ev[0]["passed"] is True and ev[0]["id"] == "E3"
    assert ev[0]["content"]["top"]["digest"].startswith("SELECT ... FOR SHARE")


def test_digest_latency_below_threshold_stays_transient():
    """耗时增量未达锁等待阈值且无扫描增量:维持暂态重采,不产证据。"""
    r = {"success": True, "data": [{"digest": "SELECT ... FOR SHARE",
                                    "rows_examined_delta": 0,
                                    "total_latency_us_delta": 2_000_000}]}
    assert _evaluate_digests(r, {"affected_operation_ref": "ORDER_CREATE"}) == []
