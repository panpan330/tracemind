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
