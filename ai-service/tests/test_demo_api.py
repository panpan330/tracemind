"""demo API 单测:demo_mode 开关 + 代理转发(httpx mock),不触 Java 服务。"""
import pytest
from fastapi.testclient import TestClient

from app.api import demo as demo_api
from app.config import settings
from app.main import app

client = TestClient(app)


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


def test_demo_disabled_returns_403(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    resp = client.post("/api/demo/scenarios/SCN-001/inject")
    assert resp.status_code == 403


def test_demo_inject_proxies(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    calls = []

    def fake_request(method, url, headers=None, timeout=None):
        calls.append((method, url, headers))
        return _FakeResp(200, {"ok": True})

    monkeypatch.setattr(demo_api.httpx, "request", fake_request)
    resp = client.post("/api/demo/scenarios/SCN-001/inject")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert calls[0][0] == "POST"
    assert "SCN-001/inject" in calls[0][1]
    assert calls[0][2] == {"x-demo-key": settings.demo_key}


def test_demo_reset_proxies(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    calls = []
    monkeypatch.setattr(demo_api.httpx, "request",
                        lambda method, url, headers=None, timeout=None:
                        calls.append((method, url)) or _FakeResp(200, {"detail": "reset"}))
    resp = client.post("/api/demo/scenarios/SCN-002/reset")
    assert resp.status_code == 200
    assert calls[0][0] == "POST"
    assert "SCN-002/reset" in calls[0][1]


def test_demo_status_proxies(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    calls = []
    monkeypatch.setattr(demo_api.httpx, "request",
                        lambda method, url, headers=None, timeout=None:
                        calls.append((method, url)) or _FakeResp(
                            200, {"indexPresent": True, "lockHeld": False}))
    resp = client.get("/api/demo/scenarios/SCN-001/status")
    assert resp.status_code == 200
    assert resp.json()["indexPresent"] is True
    assert calls[0][0] == "GET"
    assert "status" in calls[0][1]


def test_demo_proxy_upstream_error_raises(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(demo_api.httpx, "request",
                        lambda *a, **k: _FakeResp(500, {"detail": "boom"}))
    resp = client.post("/api/demo/scenarios/SCN-001/inject")
    assert resp.status_code == 500
