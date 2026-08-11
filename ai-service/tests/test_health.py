import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "mcp_ready" in body


def test_lifespan_starts_mcp(monkeypatch):
    class FakeMgr:
        def __init__(self, fixture_file=None):
            self.is_ready = True

        async def start(self):
            pass

        async def stop(self):
            pass

    monkeypatch.setattr("app.main.McpClientManager", FakeMgr)
    with TestClient(app) as c:
        assert c.get("/api/health").json()["mcp_ready"] is True


def test_lifespan_start_failure_propagates(monkeypatch):
    from app.mcp.client import MCP_START_FAILED, MCPError

    class BoomMgr:
        def __init__(self, fixture_file=None):
            pass

        async def start(self):
            raise MCPError(MCP_START_FAILED, "boom")

        async def stop(self):
            pass

    monkeypatch.setattr("app.main.McpClientManager", BoomMgr)
    with pytest.raises(MCPError):
        with TestClient(app):
            pass
