"""V2.0-A closure:恢复链路可变上下文与默认值消除的证明。

证明三件事:
1. 真实 Graph 的 get_trace/get_service_metrics 走 MCP(handler)路径,incident_id
   经 ToolExecutionService 注入,端口适配器按受控上下文解析 service/operation;
2. trace_service 关键上下文缺失 → fail closed(INCIDENT_CONTEXT_MISSING),
   不再回退 inventory-service / INVENTORY_LOOKUP 演示默认值;
3. Graph 内 _call_tool 对 MCP 契约工具不落入带默认值的 legacy 直调路径。
"""
import uuid

import pytest
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import Incident
from app.mcp.contract import TOOL_NAMES
from app.repositories import incident_repo
from app.services.trace_service import get_trace as trace_service_get_trace
from app.tools_core.context import ClientInvocationContext
from app.tools_infrastructure.investigation import build_investigation_ports


def test_real_graph_routes_trace_and_metrics_via_mcp_not_legacy_defaults():
    """路由证明:get_trace/get_service_metrics ∈ MCP 契约工具集 → nodes._call_tool
    走 MCP 客户端(上下文注入),不落入 tools/__init__ 的 legacy 直调封装。"""
    assert "get_trace" in TOOL_NAMES
    assert "get_service_metrics" in TOOL_NAMES
    # legacy 直调封装存在默认值回退,但仅服务 POST /{id}/tools 演示入口,Graph 不经过
    from app.agent import nodes
    import inspect
    src = inspect.getsource(nodes._call_tool)
    assert "TOOL_NAMES" in src and "get_mcp_client" in src


def test_trace_service_missing_context_fails_closed():
    """service/operation 缺失 → INCIDENT_CONTEXT_MISSING(不再演示默认值兜底)。"""
    from app.tools_core.errors import ToolBusinessError

    with pytest.raises(ToolBusinessError, match="INCIDENT_CONTEXT_MISSING"):
        trace_service_get_trace("REPRESENTATIVE_SLOW_TRACE", None, {})


def test_trace_service_empty_incident_object_fails_closed():
    from app.tools_core.errors import ToolBusinessError

    with pytest.raises(ToolBusinessError, match="INCIDENT_CONTEXT_MISSING"):
        trace_service_get_trace("REPRESENTATIVE_SLOW_TRACE", None,
                                {"affected_service_ref": None,
                                 "affected_operation_ref": None})


def test_mcp_chain_injects_incident_context(monkeypatch):
    """注:execute 层失败会包装为 ToolResult(success=False, error_code)而非抛出。"""
    """MCP 真实模式全链路:ctx.incident_id 注入 handler → 端口解析受控上下文
    → trace_service 收到 Incident 的 service/operation(非默认值)。"""
    inc = incident_repo.create_incident(
        "mcp 上下文注入", None, "high", "inventory-service",
        affected_service_ref="inventory-service",
        affected_operation_ref="INVENTORY_RESERVATION")

    captured = {}

    def fake_trace_service(trace_ref, trace_id, incident, incident_id=0, agent_run_id=0):
        captured["incident"] = incident
        captured["incident_id"] = incident_id
        return {"sourceBackend": "fixture", "traceId": "t-1",
                "dbDominanceRatio": 0.9, "inventoryServerDurationMs": 900}

    monkeypatch.setattr("app.tools_infrastructure.investigation.trace_service.get_trace",
                        fake_trace_service)

    ports = build_investigation_ports()
    from app.tools_core.service import ToolExecutionService

    svc = ToolExecutionService(ports=ports)
    ctx = ClientInvocationContext(
        incident_id=inc.id, agent_run_id=int(uuid.uuid4().hex[:8], 16) % 10**8,
        tool_call_id=f"tc-{uuid.uuid4().hex[:10]}", purpose="investigation")
    out = svc.execute("get_trace", {"trace_ref": "REPRESENTATIVE_SLOW_TRACE"}, ctx)
    assert out["data"]["traceId"] == "t-1"
    assert captured["incident_id"] == inc.id
    # 关键:到达 trace_service 的是受控上下文,不是空 dict/默认值
    assert captured["incident"]["affected_service_ref"] == "inventory-service"
    assert captured["incident"]["affected_operation_ref"] == "INVENTORY_RESERVATION"


def test_mcp_chain_without_incident_row_fails_closed(monkeypatch):
    """incident_id 指向不存在的行:空上下文 → trace_service fail closed(不默认兜底)。"""
    from app.tools_core.service import ToolExecutionService

    svc = ToolExecutionService(ports=build_investigation_ports())
    ctx = ClientInvocationContext(
        incident_id=987654321, agent_run_id=int(uuid.uuid4().hex[:8], 16) % 10**8,
        tool_call_id=f"tc-{uuid.uuid4().hex[:10]}", purpose="investigation")
    out = svc.execute("get_trace", {"trace_ref": "REPRESENTATIVE_SLOW_TRACE"}, ctx)
    assert out["success"] is False
    assert out["error_code"] == "INCIDENT_CONTEXT_MISSING"


def test_legacy_get_trace_direct_call_with_unknown_incident_fails_closed():
    """legacy 直调(演示 API 路径)incident 不存在 → 失败结果含精确错误码(零伪造数据)。"""
    from app.tools.execute import execute_tool
    out = execute_tool("get_trace", incident_id=987654321,
                       trace_ref="REPRESENTATIVE_SLOW_TRACE")
    assert out["success"] is False
    assert out["error_code"] == "INCIDENT_CONTEXT_MISSING"
    assert not (out["data"] or {}).get("traceId")   # 未伪造任何 trace 数据
