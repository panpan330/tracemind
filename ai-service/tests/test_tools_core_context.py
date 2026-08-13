# ai-service/tests/test_tools_core_context.py
import pytest
from app.tools_core.context import (
    ClientInvocationContext, AuthenticatedPrincipal, ServerInvocationContext,
    Purpose, RESERVED_HEADERS,
)
from app.tools_core.errors import ToolBusinessError


def test_client_context_requires_all_fields():
    # dataclass 缺必填字段抛 TypeError;__post_init__ 校验非法值抛 ValueError
    with pytest.raises((ValueError, TypeError)):
        ClientInvocationContext(incident_id=1, agent_run_id=1)  # 缺 tool_call_id/purpose


def test_purpose_enum_values():
    assert {p.value for p in Purpose} == {"investigation", "recovery_verification"}


def test_principal_holds_token_fingerprint():
    p = AuthenticatedPrincipal(client_id="ai-service", subject="ai-service",
                               audience="tracemind-mcp-tools", scopes=["tools:investigate"],
                               token_fingerprint="sha256:abc")
    assert p.client_id == "ai-service" and p.scopes == ["tools:investigate"]


def test_server_context_composes():
    c = ClientInvocationContext(incident_id=1, agent_run_id=2, tool_call_id="tc-1",
                                purpose="investigation")
    p = AuthenticatedPrincipal(client_id="ai-service", subject="ai-service",
                               audience="tracemind-mcp-tools", scopes=["tools:investigate"],
                               token_fingerprint="fp")
    s = ServerInvocationContext(client=c, principal=p, trace_context="00-abc-def-01",
                                protocol_version="2026-07-28", mcp_request_id="m-1")
    assert s.client.tool_call_id == "tc-1" and s.principal.client_id == "ai-service"


def test_reserved_headers_exact_set():
    assert RESERVED_HEADERS == {
        "X-TraceMind-Incident-Id", "X-TraceMind-Agent-Run-Id", "X-TraceMind-Tool-Call-Id",
        "X-TraceMind-Purpose", "X-TraceMind-Context-Version",
    }


def test_business_error_retryable():
    e = ToolBusinessError(code="TRACE_NOT_FOUND", message="no trace", retryable=False)
    assert e.code == "TRACE_NOT_FOUND" and e.retryable is False
    assert "TRACE_NOT_FOUND" in str(e)
