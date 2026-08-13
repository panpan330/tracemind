"""InvocationContext 定义(传输无关)。

- ClientInvocationContext:Client 唯一允许注入的业务调查上下文(逐请求生成)。
- AuthenticatedPrincipal:只能由认证结果派生,Client 无权构造。
- ServerInvocationContext:Server 侧完整上下文(Client + Principal + 传输/协议)。
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Purpose(str, Enum):
    INVESTIGATION = "investigation"
    RECOVERY_VERIFICATION = "recovery_verification"


# Client 唯一允许注入的受控 Header(逐请求生成,不存共享 Client Header)
RESERVED_HEADERS = frozenset({
    "X-TraceMind-Incident-Id", "X-TraceMind-Agent-Run-Id", "X-TraceMind-Tool-Call-Id",
    "X-TraceMind-Purpose", "X-TraceMind-Context-Version",
})


@dataclass(frozen=True)
class ClientInvocationContext:
    incident_id: int
    agent_run_id: int
    tool_call_id: str
    purpose: str

    def __post_init__(self) -> None:
        if not (self.incident_id > 0 and self.agent_run_id > 0):
            raise ValueError("incident_id/agent_run_id 必须为正整数")
        if not self.tool_call_id or len(self.tool_call_id) > 64:
            raise ValueError("tool_call_id 非法")
        if self.purpose not in {p.value for p in Purpose}:
            raise ValueError(f"purpose 非法: {self.purpose}")


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """只能由认证结果派生,Client 无权构造。"""
    client_id: str
    subject: str
    audience: str
    scopes: list = field(default_factory=list)
    token_fingerprint: str = ""


@dataclass(frozen=True)
class ServerInvocationContext:
    client: ClientInvocationContext
    principal: AuthenticatedPrincipal
    trace_context: Optional[str] = None      # W3C traceparent
    protocol_version: Optional[str] = None   # negotiated_protocol_version
    mcp_request_id: Optional[str] = None
