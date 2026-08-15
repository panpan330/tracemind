"""V1.11 CostTracker 单测。"""
import pytest

from app.agent import cost


def test_aggregate_by_model():
    calls = [
        {"model": "qwen3.8-max", "input_tokens": 1000, "output_tokens": 200},
        {"model": "qwen3.8-max", "input_tokens": 500, "output_tokens": 100},
        {"model": "qwen3.7-flash", "input_tokens": 2000, "output_tokens": 300},
    ]
    out = cost.aggregate_model_costs(calls)
    assert out["qwen3.8-max"]["calls"] == 2
    assert out["qwen3.8-max"]["input_tokens"] == 1500
    assert out["qwen3.8-max"]["cost"] == pytest.approx(
        cost.MODEL_PRICE_PER_M["qwen3.8-max"] * 1800 / 1_000_000, rel=1e-3)


def test_aggregate_unknown_model_cost_zero():
    out = cost.aggregate_model_costs(
        [{"model": "some-unknown", "input_tokens": 100, "output_tokens": 10}])
    assert out["some-unknown"]["cost"] == 0


def test_aggregate_empty():
    assert cost.aggregate_model_costs([]) == {}


def test_aggregate_decimal_tokens():
    """数据库返回 Decimal(如 SUM 聚合)→ 成本计算不崩溃。"""
    from decimal import Decimal
    out = cost.aggregate_model_costs(
        [{"model": "qwen3.7-flash", "input_tokens": Decimal("100"), "output_tokens": Decimal("50")}])
    assert out["qwen3.7-flash"]["cost"] == cost.MODEL_PRICE_PER_M["qwen3.7-flash"] * 150 / 1_000_000


def test_check_cost_budget_under_budget(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "cost_budget", 100.0)
    calls = [{"model": "qwen3.7-flash", "input_tokens": 100, "output_tokens": 50}]
    assert cost.check_cost_budget(calls) is False


def test_check_cost_budget_over_budget(monkeypatch):
    from app.config import settings
    from app.repositories import event_repo
    monkeypatch.setattr(settings, "cost_budget", 0.00001)   # 极小预算必超
    appended = []
    monkeypatch.setattr(event_repo, "append_event",
                        lambda *a, **k: appended.append((a, k)) or type("E", (), {"sequence": 1})())
    calls = [{"model": "qwen3.7-flash", "input_tokens": 100000, "output_tokens": 50000}]
    assert cost.check_cost_budget(calls) is True
    assert appended, "超预算应写 cost_over_budget 事件"


def test_check_cost_budget_disabled(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "cost_budget", 0.0)
    assert cost.check_cost_budget([]) is False
