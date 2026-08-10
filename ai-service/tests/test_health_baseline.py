"""健康指标基线采集与真实基线恢复判定。"""
from unittest.mock import patch

import pytest

from app.services.health_baseline_service import capture_health_baseline
from app.services.recovery_service import _p95_recovered


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
        baseline = capture_health_baseline("inventory-service")
    assert baseline == {"p95_ms": 2, "qps": 20.0, "error_rate": 0.0}
    m.assert_called_once()


def test_capture_health_baseline_unavailable_returns_none():
    with patch("app.services.health_baseline_service.httpx.get",
               side_effect=Exception("connection refused")):
        assert capture_health_baseline("inventory-service") is None


def test_capture_health_baseline_null_p95_returns_none():
    with patch("app.services.health_baseline_service.httpx.get",
               return_value=FakeMetricsResponseNull()):
        assert capture_health_baseline("inventory-service") is None


@pytest.mark.parametrize("p95_after,baseline,expected", [
    (2, {"p95_ms": 2}, True),      # 等于基线 -> 恢复
    (3, {"p95_ms": 2}, False),     # 3 > 2.4(2×1.2)-> 未恢复
    (2, None, True),               # 基线缺失 -> 视为通过
    (2, {"p95_ms": None}, True),   # 基线 P95 缺失 -> 视为通过
])
def test_p95_recovery_rule(p95_after, baseline, expected):
    assert _p95_recovered(p95_after, baseline) is expected
