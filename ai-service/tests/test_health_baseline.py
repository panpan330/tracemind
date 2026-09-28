"""健康指标基线采集与真实基线恢复判定。"""
from unittest.mock import patch

import pytest

from app.services.health_baseline_service import capture_current_health_snapshot
from app.services import recovery_signal


class FakeMetricsResponse:
    status_code = 200

    def json(self):
        return {"service": "inventory-service", "window_seconds": 300,
                "p95_ms": 2, "qps": 20.0, "error_rate": 0.0}

    def raise_for_status(self):
        return None


class FakeMetricsResponseNull:
    status_code = 200

    def json(self):
        return {"service": "inventory-service", "window_seconds": 300,
                "p95_ms": None, "qps": 20.0, "error_rate": None}

    def raise_for_status(self):
        return None


def test_capture_health_baseline_ok():
    with patch("app.services.health_baseline_service.httpx.get",
               return_value=FakeMetricsResponse()) as m:
        baseline = capture_current_health_snapshot("inventory-service")
    assert baseline == {"p95_ms": 2, "qps": 20.0, "error_rate": 0.0,
                      "captured_at_is_current": True}
    m.assert_called_once()


def test_capture_health_baseline_unavailable_returns_none():
    with patch("app.services.health_baseline_service.httpx.get",
               side_effect=Exception("connection refused")):
        assert capture_current_health_snapshot("inventory-service") is None


def test_capture_health_baseline_null_p95_returns_none():
    with patch("app.services.health_baseline_service.httpx.get",
               return_value=FakeMetricsResponseNull()):
        assert capture_current_health_snapshot("inventory-service") is None


@pytest.mark.parametrize("p95_after,baseline,expected", [
    (2, {"p95_ms": 2}, True),      # 等于基线 -> 恢复
    (3, {"p95_ms": 2}, False),     # 3 > 2.4(2×1.2)-> 未恢复
    (2, None, True),               # 无合格基线 -> 显式 SLO(100)判定,非"默认通过"
    (2, {"p95_ms": None}, True),   # 基线 P95 缺失 -> SLO 判定
    (300, None, False),            # 超过 SLO → 未恢复(旧 fail-open 会误判"通过")
])
def test_p95_recovery_rule(p95_after, baseline, expected):
    threshold, source = recovery_signal.threshold_for(baseline)
    assert (p95_after <= threshold) is expected
    assert source == ("baseline" if (baseline or {}).get("p95_ms") else "SLO")
