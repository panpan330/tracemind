# ai-service/tests/test_tools_core_service.py
import hashlib
import json

import pytest

from app.tools_core.service import ToolExecutionService
from app.tools_core.context import ClientInvocationContext
from app.tools_core.errors import ToolBusinessError


def _ctx():
    return ClientInvocationContext(incident_id=1, agent_run_id=1, tool_call_id="tc-1",
                                   purpose="investigation")


def _fx_key(name, args):
    h = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:12]
    return f"{name}:{h}"


def test_fixture_hit():
    svc = ToolExecutionService(ports={}, runtime="fixture")
    args = {"table_ref": "inventory"}
    svc.set_fixture({_fx_key("get_index_info", args): {"ok": True, "data": {"idx": 1}}})
    r = svc.execute("get_index_info", args, _ctx())
    assert r["success"] is True and r["data"] == {"idx": 1}


def test_unknown_tool():
    svc = ToolExecutionService(ports={}, runtime="real")
    r = svc.execute("nope", {}, _ctx())
    assert r["success"] is False and r["error_code"] == "UNKNOWN_TOOL"


def test_context_spoofing_rejected():
    svc = ToolExecutionService(ports={}, runtime="real")
    with pytest.raises(ToolBusinessError) as ei:
        svc.execute("get_index_info", {"incident_id": 999, "table_ref": "inventory"}, _ctx())
    assert ei.value.code == "MCP_CONTEXT_SPOOFING_REJECTED"


def test_fixture_forbidden_in_real_runtime():
    svc = ToolExecutionService(ports={}, runtime="real")
    with pytest.raises(ToolBusinessError) as ei:
        svc.set_fixture({"x": {"ok": True}})
    assert ei.value.code == "FIXTURE_FORBIDDEN"


def test_legacy_execute_thin_wrapper():
    # 薄封装回退 registry fn 路径(真实工具);只验证不抛异常且返回 ToolResult 结构
    from app.tools.execute import execute_tool
    r = execute_tool("get_service_metrics", incident_id=None, service_ref="inventory-service",
                     window_seconds=300)
    assert isinstance(r, dict) and "success" in r


def test_digest_handler_receives_incident_id():
    """digest 工具强依赖 incident_id(基线差值),handler 必须把可信 ctx.incident_id 传给端口。"""
    received = {}

    class MemDigest:
        def list_expensive_digests(self, incident_id, window_seconds=None):
            received["incident_id"] = incident_id
            return {"digests": [], "has_more": False}

    import app.tools  # noqa: F401  确保 TOOL_REGISTRY 已注册
    svc = ToolExecutionService(ports={"digest": MemDigest()}, runtime="real")
    import uuid
    ctx = ClientInvocationContext(incident_id=1, agent_run_id=1,
                                  tool_call_id=f"tc-{uuid.uuid4().hex[:8]}",
                                  purpose="investigation")
    svc.execute("list_expensive_query_digests", {"window_seconds": 300}, ctx)
    assert received.get("incident_id") == 1, f"digest 端口收到 incident_id={received}"
