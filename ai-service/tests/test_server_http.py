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


def test_http_app_accepts_token(monkeypatch, tmp_path):
    import hashlib, json
    from starlette.testclient import TestClient
    token = "ai-token-test"
    fp = "sha256:" + hashlib.sha256(token.encode()).hexdigest()
    f = tmp_path / "clients.json"
    f.write_text(json.dumps({fp: {"subject": "ai-service",
                                  "audience": "tracemind-mcp-tools",
                                  "scopes": ["tools:investigate"]}}), encoding="utf-8")
    monkeypatch.setenv("TRACEMIND_MCP_TRANSPORT", "streamable_http")
    monkeypatch.setenv("TRACEMIND_MCP_AUTH_CLIENTS_FILE", str(f))
    app = create_http_app()
    with TestClient(app) as c:
        r = c.post("/mcp", json={}, headers={"Authorization": f"Bearer {token}"})
        # 认证通过后进入 MCP 协议层(JSON-RPC 解析);不再 401
        assert r.status_code != 401
        r_no = c.post("/mcp", json={})
        assert r_no.status_code == 401
