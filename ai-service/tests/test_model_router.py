"""V1.11 ModelRouter 单测。"""
from app.agent import model_router
from app.config import settings


def test_route_configured_node(monkeypatch):
    monkeypatch.setattr(settings, "select_tool_model", "qwen3.7-flash")
    assert model_router.route("select_tool") == "qwen3.7-flash"


def test_route_unconfigured_node_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "hypothesize_model", "")
    assert model_router.route("hypothesize") is None


def test_route_unknown_node_returns_none(monkeypatch):
    assert model_router.route("unknown_node") is None


def test_route_all_empty_returns_none(monkeypatch):
    for k in model_router.NODE_MODEL_KEY.values():
        monkeypatch.setattr(settings, k, "")
    assert model_router.route("select_tool") is None
