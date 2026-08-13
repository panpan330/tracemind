# ai-service/tests/test_mcp_security.py
import hashlib
import json

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.mcp.security import build_security_middleware, fingerprint, load_clients
from app.mcp.protocol_errors import (ProtocolError, MCP_TOOL_NOT_FOUND,
                                     MCP_PROTOCOL_VERSION_UNSUPPORTED)
from app.mcp.client_errors import (ClientError, MCP_AUTH_FAILED, MCP_DISCONNECTED,
                                   HTTP_RETRYABLE)


def test_fingerprint_stable():
    assert fingerprint("secret") == "sha256:" + hashlib.sha256(b"secret").hexdigest()


def test_load_clients(tmp_path):
    f = tmp_path / "clients.json"
    f.write_text(json.dumps({
        fingerprint("ai-token"): {"subject": "ai-service", "audience": "tracemind-mcp-tools",
                                  "scopes": ["tools:investigate"]}
    }), encoding="utf-8")
    clients = load_clients(str(f))
    assert clients[fingerprint("ai-token")]["subject"] == "ai-service"


def test_auth_middleware_401_without_token():
    async def ok(request):
        return JSONResponse({"p": request.state.principal.client_id})
    app = Starlette(routes=[Route("/mcp", ok, methods=["POST"])],
                    middleware=build_security_middleware(clients_file=None))
    with TestClient(app) as c:
        r = c.post("/mcp", json={})
        assert r.status_code == 401


def test_auth_middleware_accepts_token(tmp_path):
    f = tmp_path / "clients.json"
    f.write_text(json.dumps({
        fingerprint("ai-token"): {"subject": "ai-service", "audience": "tracemind-mcp-tools",
                                  "scopes": ["tools:investigate"]}
    }), encoding="utf-8")

    async def ok(request):
        return JSONResponse({"p": request.state.principal.client_id})
    app = Starlette(routes=[Route("/mcp", ok, methods=["POST"])],
                    middleware=build_security_middleware(clients_file=str(f)))
    with TestClient(app) as c:
        r = c.post("/mcp", json={}, headers={"Authorization": "Bearer ai-token"})
        assert r.status_code == 200
        assert r.json()["p"] == "ai-service"


def test_auth_middleware_rejects_bad_token(tmp_path):
    f = tmp_path / "clients.json"
    f.write_text(json.dumps({
        fingerprint("ai-token"): {"subject": "ai-service", "audience": "tracemind-mcp-tools",
                                  "scopes": ["tools:investigate"]}
    }), encoding="utf-8")

    async def ok(request):
        return JSONResponse({"p": "x"})
    app = Starlette(routes=[Route("/mcp", ok, methods=["POST"])],
                    middleware=build_security_middleware(clients_file=str(f)))
    with TestClient(app) as c:
        r = c.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401


def test_protocol_errors_layer():
    assert issubclass(ProtocolError, Exception)
    assert MCP_TOOL_NOT_FOUND == "MCP_TOOL_NOT_FOUND"
    assert MCP_PROTOCOL_VERSION_UNSUPPORTED == "MCP_PROTOCOL_VERSION_UNSUPPORTED"


def test_client_errors_retryable_map():
    assert HTTP_RETRYABLE[429] is True and HTTP_RETRYABLE[503] is True
    assert HTTP_RETRYABLE[401] is False and HTTP_RETRYABLE[413] is False
    e = ClientError(MCP_DISCONNECTED, retryable=True)
    assert e.retryable is True
    assert ClientError(MCP_AUTH_FAILED, retryable=False).retryable is False
