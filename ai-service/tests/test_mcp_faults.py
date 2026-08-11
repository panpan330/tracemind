"""故障注入:错误码映射与"未就绪/终止不降级 direct"。"""
import pytest

from app.mcp.client import (MCP_DISCONNECTED, MCP_START_FAILED, MCP_TIMEOUT,
                            McpClientManager, MCPError)


def test_start_failed_maps_code():
    """未就绪调用 → 明确错误码,而非静默 direct。"""
    mgr = McpClientManager()
    with pytest.raises(MCPError) as ei:
        mgr.call_tool("get_trace", incident_id=1, agent_run_id=1, trace_id="t")
    assert ei.value.code in (MCP_DISCONNECTED, MCP_START_FAILED)


def test_disconnected_after_terminate():
    """主动终止(MCP Server 未启动/已终止)后调用返回明确 MCP 错误,不得降级 direct。"""
    mgr = McpClientManager()
    with pytest.raises(MCPError) as ei:
        mgr.call_tool("get_trace", incident_id=1, agent_run_id=1, trace_id="t")
    # 不是业务错误码(TRACE_NOT_FOUND 等),而是 MCP 基础设施错误
    assert ei.value.code in (MCP_DISCONNECTED, MCP_START_FAILED, MCP_TIMEOUT)
