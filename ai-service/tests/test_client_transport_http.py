# ai-service/tests/test_client_transport_http.py
import pytest

from app.config.mcp import McpClientSettings
from app.mcp.client_errors import ClientError, MCP_AUTH_FAILED, MCP_DISCONNECTED
from app.tools_core.context import ClientInvocationContext


class FakeMcpClient:  # 模拟 SDK streamablehttp_client 会话
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.proto = "2026-07-28"

    async def initialize(self):
        return type("R", (), {"protocolVersion": self.proto, "serverInfo": type("I", (), {
            "name": "tracemind-mcp-tools", "version": "1.0"})})

    async def list_tools(self):
        return type("R", (), {"tools": []})

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        resp = self.responses.pop(0) if self.responses else "{\"success\": true}"
        if isinstance(resp, Exception):
            raise resp
        return type("R", (), {"content": [type("C", (), {"type": "text", "text": resp})]})


@pytest.mark.asyncio
async def test_call_injects_headers_per_request():
    from app.mcp.client_transport_http import McpHttpTransport
    t = McpHttpTransport(settings=McpClientSettings())
    ctx = ClientInvocationContext(1, 1, "tc-1", "investigation")
    h1 = t._build_headers(ctx)
    ctx2 = ClientInvocationContext(2, 2, "tc-2", "recovery_verification")
    h2 = t._build_headers(ctx2)
    assert h1["X-TraceMind-Incident-Id"] == "1" and h2["X-TraceMind-Incident-Id"] == "2"
    assert h1["X-TraceMind-Tool-Call-Id"] == "tc-1" and h2["X-TraceMind-Tool-Call-Id"] == "tc-2"
    assert h1["X-TraceMind-Purpose"] == "investigation"
    assert h2["X-TraceMind-Purpose"] == "recovery_verification"
    assert h1["X-TraceMind-Context-Version"] == "1"


@pytest.mark.asyncio
async def test_auth_failure_not_retried():
    from app.mcp.client_transport_http import McpHttpTransport
    t = McpHttpTransport(settings=McpClientSettings())
    t._session = FakeMcpClient([ClientError(MCP_AUTH_FAILED, retryable=False)])
    with pytest.raises(ClientError) as ei:
        await t._call_with_retry("get_trace", {}, ClientInvocationContext(1, 1, "tc-1", "investigation"))
    assert ei.value.code == MCP_AUTH_FAILED


@pytest.mark.asyncio
async def test_retryable_error_retries_once():
    from app.mcp.client_transport_http import McpHttpTransport
    t = McpHttpTransport(settings=McpClientSettings())
    t._session = FakeMcpClient([ClientError(MCP_DISCONNECTED, retryable=True),
                                "{\"success\": true}"])
    out = await t._call_with_retry("get_trace", {}, ClientInvocationContext(1, 1, "tc-1", "investigation"))
    assert out["success"] is True
    assert len(t._session.calls) == 2


@pytest.mark.asyncio
async def test_disconnected_without_session():
    from app.mcp.client_transport_http import McpHttpTransport
    t = McpHttpTransport(settings=McpClientSettings())
    with pytest.raises(ClientError) as ei:
        await t.call_tool("get_trace", {}, ClientInvocationContext(1, 1, "tc-1", "investigation"))
    assert ei.value.code == MCP_DISCONNECTED
