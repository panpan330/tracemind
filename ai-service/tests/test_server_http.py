# ai-service/tests/test_server_http.py
import os

from app.mcp.server_http import create_http_app


def test_http_app_created(monkeypatch):
    monkeypatch.setenv("TRACEMIND_MCP_TRANSPORT", "streamable_http")
    monkeypatch.setenv("TRACEMIND_MCP_AUTH_CLIENTS_FILE", "/tmp/not-exist.json")
    app = create_http_app()
    assert app is not None
    # 路由包含 /mcp 与 /health/live、/health/ready
    routes = [getattr(r, "path", "") for r in app.routes]
    assert "/mcp" in routes
    assert "/health/live" in routes
    assert "/health/ready" in routes
