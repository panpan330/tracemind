"""java_client(httpx mock)与 tools.registry 单测:不触真实服务/DB。"""
import httpx
import pytest

from app.services import java_client
from app.tools_core import registry


# ---------- java_client ----------

class FakeResp:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)
        return None

    def json(self):
        return self._payload


def test_base_url_order_and_inventory(monkeypatch):
    assert java_client._base_url("order-service") == java_client._ORDER
    assert java_client._base_url("inventory-service") == java_client._INVENTORY


def test_base_url_unknown_raises():
    with pytest.raises(ValueError, match="UNKNOWN_SERVICE_REF"):
        java_client._base_url("payment-service")


def test_get_metrics_passes_params(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return FakeResp({"p95Ms": 5})

    monkeypatch.setattr(httpx, "get", fake_get)
    out = java_client.get_metrics("order-service", window_seconds=60)
    assert out == {"p95Ms": 5}
    assert captured["url"] == f"{java_client._ORDER}/internal/observations/metrics"
    assert captured["params"] == {"window_seconds": 60}


def test_get_trace_records_found(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp([{"traceId": "t1"}]))
    out = java_client.get_trace_records("inventory-service", "trace-1")
    assert out == [{"traceId": "t1"}]


def test_get_trace_records_404_returns_none(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp(None, status_code=404))
    assert java_client.get_trace_records("order-service", "missing") is None


def test_get_trace_records_500_raises(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp(None, status_code=500))
    with pytest.raises(httpx.HTTPStatusError):
        java_client.get_trace_records("order-service", "x")


# ---------- tools.registry ----------

def test_tool_decorator_registers_spec():
    from pydantic import BaseModel

    class In(BaseModel):
        q: str

    def _fn(_in: In) -> dict:
        return {"ok": True}

    registry.TOOL_REGISTRY.pop("probe_tool", None)
    registry.tool("probe_tool", In)(_fn)
    spec = registry.TOOL_REGISTRY["probe_tool"]
    assert spec.name == "probe_tool"
    assert spec.input_schema is In
    assert spec.fn is _fn
    # 注册后 fn 原样返回
    assert _fn(In(q="x")) == {"ok": True}
