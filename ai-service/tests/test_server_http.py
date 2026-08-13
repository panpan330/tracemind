# ai-service/tests/test_server_http.py
import hashlib
import json
import os

import pytest

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


@pytest.mark.asyncio
async def test_http_app_rejects_without_token(monkeypatch, tmp_path):
    # 认证层验证:无 token 的 /mcp 请求被拦截(401);完整 MCP 协商留 VM 验收(uvicorn asyncio 环境)
    import httpx
    import hashlib
    token = "ai-token-test"
    fp = "sha256:" + hashlib.sha256(token.encode()).hexdigest()
    f = tmp_path / "clients.json"
    f.write_text(json.dumps({fp: {"subject": "ai-service",
                                  "audience": "tracemind-mcp-tools",
                                  "scopes": ["tools:investigate"]}}), encoding="utf-8")
    monkeypatch.setenv("TRACEMIND_MCP_TRANSPORT", "streamable_http")
    monkeypatch.setenv("TRACEMIND_MCP_AUTH_CLIENTS_FILE", str(f))
    app = create_http_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r_no = await c.post("/mcp", json={})
        assert r_no.status_code == 401


@pytest.mark.asyncio
async def test_health_ready_skips_auth(monkeypatch):
    import httpx
    monkeypatch.setenv("TRACEMIND_MCP_TRANSPORT", "streamable_http")
    app = create_http_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/health/ready")
        assert r.status_code in (200, 503)   # 不 401(运维端点不认证)
