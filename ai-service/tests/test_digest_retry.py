"""E3 digest 评估:增量全 0 = 暂态(重采);增量>阈值 = 正向;否则确定性否定。"""
from app.agent.nodes import _evaluate_digests


def test_digest_delta_zero_is_transient():
    r = {"success": True, "data": [{"digest": "SELECT ... FOR SHARE",
                                    "rows_examined_delta": 0}]}
    assert _evaluate_digests(r, {}) == []


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
