"""V2.1-C 提交②:恢复信号 —— 与告警同口径的 HTTP P95(通用/锁验证器共用唯一实现)。

覆盖:
- 窗口必须**完全位于恢复信号之后**(不能只凭滚动 rate 查询的结果时间戳);
- 信号后新请求样本数下限(无流量 → INCONCLUSIVE,不得宣告恢复);
- 阈值来源:合格基线(×1.2)或显式 SLO;两者都不可判定 → INCONCLUSIVE;
- 服务/操作取冻结 RunContext(与告警同服务同操作),operation 决定 uri 正则。
"""
from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.services import recovery_signal as rs
from app.services.operation_registry import uri_regex_for_operation


class FakeProm:
    def __init__(self, *, p95_ms=30.0, samples=100):
        self.p95_ms = p95_ms
        self.samples = samples
        self.query_calls = []
        self.count_calls = []

    def query_at(self, template_id, labels, window_seconds, at_time):
        self.query_calls.append((template_id, dict(labels), window_seconds, at_time))
        value = self.p95_ms / 1000.0
        return [{"metric": {}, "value": [at_time, str(value)]}]

    def sample_count(self, template_id, labels, start, end):
        self.count_calls.append((template_id, dict(labels), start, end))
        return self.samples


def _signal_at():
    return datetime(2026, 9, 28, 6, 0, 0)


def test_window_must_be_fully_after_signal(monkeypatch):
    """窗口起点必须 ≥ 信号时刻:信号刚过、窗口仍覆盖信号前流量 → 不判定(继续等待)。"""
    fake = FakeProm()
    monkeypatch.setattr(rs, "_client", lambda: fake)
    monkeypatch.setattr(settings, "recovery_signal_window_s", 60)
    # now 距信号仅 30s < 窗口 60s → 窗口未被信号后流量填满
    out = rs.measure_post_signal_p95("inventory-service", "INVENTORY_LOOKUP",
                                     _signal_at(), baseline=None,
                                     now=_signal_at() + timedelta(seconds=30),
                                     wait_seconds=0)
    assert out.status == rs.STATUS_INCONCLUSIVE
    assert out.reason == "post_signal_window_incomplete"
    assert fake.query_calls == []                     # 不做无效查询


def test_post_signal_window_uses_explicit_time(monkeypatch):
    """窗口完整位于信号后 → 评估点=now、rate 窗口=配置窗口,且服务/操作口径来自冻结上下文。"""
    fake = FakeProm(p95_ms=30.0)
    monkeypatch.setattr(rs, "_client", lambda: fake)
    monkeypatch.setattr(settings, "recovery_signal_window_s", 60)
    now = _signal_at() + timedelta(seconds=120)
    out = rs.measure_post_signal_p95("order-service", "ORDER_CREATE", _signal_at(),
                                     baseline={"p95_ms": 50}, now=now, wait_seconds=0)
    assert out.status == rs.STATUS_RECOVERED               # 30 <= 50*1.2
    assert out.source == rs.SOURCE_BASELINE
    assert out.threshold_ms == 60.0
    tpl, labels, window, at_time = fake.query_calls[0]
    assert tpl == "HTTP_SERVER_P95_V1"
    assert window == 60
    assert abs(at_time - now.timestamp()) < 1
    # 窗口起点 = at_time - 60s ≥ signal_at(信号后新请求)
    _, _, cstart, cend = fake.count_calls[0]
    assert cstart >= _signal_at().timestamp()
    assert labels["uri"] == uri_regex_for_operation("order-service", "ORDER_CREATE")


def test_no_post_signal_traffic_is_inconclusive(monkeypatch):
    """信号后无新请求(样本数不足)→ INCONCLUSIVE,不得凭空窗口宣告恢复。"""
    fake = FakeProm(samples=0)
    monkeypatch.setattr(rs, "_client", lambda: fake)
    monkeypatch.setattr(settings, "recovery_signal_window_s", 60)
    out = rs.measure_post_signal_p95("order-service", "ORDER_CREATE", _signal_at(),
                                     baseline={"p95_ms": 50},
                                     now=_signal_at() + timedelta(seconds=120),
                                     wait_seconds=0)
    assert out.status == rs.STATUS_INCONCLUSIVE
    assert out.reason == "insufficient_post_signal_samples"
    assert out.sample_count == 0


def test_threshold_source_slo_when_no_qualified_baseline(monkeypatch):
    """无合格基线 → 显式 SLO;超 SLO → not_recovered(来源=SLO)。"""
    fake = FakeProm(p95_ms=settings.slo_p95_ms * 3)
    monkeypatch.setattr(rs, "_client", lambda: fake)
    monkeypatch.setattr(settings, "recovery_signal_window_s", 60)
    out = rs.measure_post_signal_p95("order-service", "ORDER_CREATE", _signal_at(),
                                     baseline=None,
                                     now=_signal_at() + timedelta(seconds=120),
                                     wait_seconds=0)
    assert out.status == rs.STATUS_NOT_RECOVERED
    assert out.source == rs.SOURCE_SLO
    assert out.threshold_ms == float(settings.slo_p95_ms)


def test_metrics_unavailable_is_inconclusive(monkeypatch):
    """观测后端不可用 → INCONCLUSIVE(不宣告恢复、也不宣告失败)。"""
    def _boom():
        raise RuntimeError("prometheus down")

    monkeypatch.setattr(rs, "_client", _boom)
    monkeypatch.setattr(settings, "recovery_signal_window_s", 60)
    out = rs.measure_post_signal_p95("order-service", "ORDER_CREATE", _signal_at(),
                                     baseline=None,
                                     now=_signal_at() + timedelta(seconds=120),
                                     wait_seconds=0)
    assert out.status == rs.STATUS_INCONCLUSIVE
    assert out.reason == "metrics_unavailable"


def test_waits_for_window_then_succeeds(monkeypatch):
    """信号刚过 → 有界等待窗口填满;填满后正常判定(不把等待当成恢复)。"""
    fake = FakeProm(p95_ms=10.0, samples=100)
    monkeypatch.setattr(rs, "_client", lambda: fake)
    monkeypatch.setattr(settings, "recovery_signal_window_s", 60)
    monkeypatch.setattr(settings, "recovery_poll_interval_s", 0.01)
    out = rs.measure_post_signal_p95("order-service", "ORDER_CREATE", _signal_at(),
                                     baseline={"p95_ms": 50},
                                     now=_signal_at() + timedelta(seconds=10),
                                     wait_seconds=2)
    assert out.status == rs.STATUS_RECOVERED


def test_current_p95_for_preflight_has_no_signal_constraint(monkeypatch):
    """执行前复核用"当前是否仍异常":取最新窗口(无信号后约束),用于拒绝已自愈的写操作。"""
    fake = FakeProm(p95_ms=5.0)
    monkeypatch.setattr(rs, "_client", lambda: fake)
    monkeypatch.setattr(settings, "recovery_signal_window_s", 60)
    out = rs.measure_current_p95("order-service", "ORDER_CREATE",
                                 baseline={"p95_ms": 50},
                                 now=_signal_at() + timedelta(seconds=120))
    assert out.status == rs.STATUS_RECOVERED
    assert out.p95_ms == 5.0
