"""MCP Server 工厂:同一工厂 + 同一 ToolRegistry,stdio 与 HTTP 为不同进程实例。

- runtime="fixture" / "stdio":子进程(离线评测/本地),transport 审计字段 mcp_stdio
- runtime="real":HTTP Server 进程,transport 审计字段 mcp_streamable_http
"""
import uuid
from typing import Optional

from app.mcp.contract import SERVER_NAME, SERVER_VERSION
from app.tools_core.context import ClientInvocationContext
from app.tools_core.ports import ToolAuditPort
from app.tools_core.service import ToolExecutionService


def create_mcp_server(runtime: str = "real",
                      audit: Optional[ToolAuditPort] = None,
                      fixture: Optional[dict] = None,
                      ports: Optional[dict] = None):
    """runtime: real | fixture | stdio。"""
    from mcp.server.fastmcp import FastMCP

    transport = "mcp_stdio" if runtime in ("stdio", "fixture") else "mcp_streamable_http"
    if ports is None:
        if runtime == "fixture":
            ports = {}
        else:
            from app.tools_infrastructure.investigation import build_investigation_ports
            ports = build_investigation_ports()
    svc = ToolExecutionService(ports=ports,
                               runtime="fixture" if runtime == "fixture" else "real",
                               fixture=fixture, audit=audit)

    mcp = FastMCP(SERVER_NAME)
    try:
        mcp._mcp_server.version = SERVER_VERSION
    except AttributeError:
        pass

    def _delegate(name: str, incident_id: int, agent_run_id: int, **business) -> dict:
        ctx = ClientInvocationContext(incident_id=incident_id or 0,
                                      agent_run_id=agent_run_id or 0,
                                      tool_call_id=f"mcp-{name}-{uuid.uuid4().hex[:8]}",
                                      purpose="investigation")
        return svc.execute(name, business, ctx, transport=transport)

    @mcp.tool()
    def get_service_metrics(incident_id: int, agent_run_id: int,
                            service_ref: str, window_seconds: int,
                            window_start: Optional[str] = None,
                            window_end: Optional[str] = None) -> dict:
        return _delegate("get_service_metrics", incident_id, agent_run_id,
                         service_ref=service_ref, window_seconds=window_seconds,
                         window_start=window_start, window_end=window_end)

    @mcp.tool()
    def get_trace(incident_id: int, agent_run_id: int,
                  trace_ref: Optional[str] = None, trace_id: Optional[str] = None) -> dict:
        return _delegate("get_trace", incident_id, agent_run_id,
                         trace_ref=trace_ref, trace_id=trace_id)

    @mcp.tool()
    def list_expensive_query_digests(incident_id: int, agent_run_id: int,
                                     window_seconds: Optional[int] = None) -> dict:
        return _delegate("list_expensive_query_digests", incident_id, agent_run_id,
                         window_seconds=window_seconds)

    @mcp.tool()
    def get_query_plan(incident_id: int, agent_run_id: int, query_ref: str,
                       sample_parameters: dict) -> dict:
        return _delegate("get_query_plan", incident_id, agent_run_id,
                         query_ref=query_ref, sample_parameters=sample_parameters)

    @mcp.tool()
    def get_index_info(incident_id: int, agent_run_id: int, table_ref: str) -> dict:
        return _delegate("get_index_info", incident_id, agent_run_id, table_ref=table_ref)

    @mcp.tool()
    def get_lock_waiters(incident_id: int, agent_run_id: int, scope_ref: str) -> dict:
        return _delegate("get_lock_waiters", incident_id, agent_run_id, scope_ref=scope_ref)

    @mcp.tool()
    def get_transaction_details(incident_id: int, agent_run_id: int,
                                transaction_ref: str) -> dict:
        return _delegate("get_transaction_details", incident_id, agent_run_id,
                         transaction_ref=transaction_ref)

    return mcp
