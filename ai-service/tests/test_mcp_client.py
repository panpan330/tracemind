"""McpClientManager 单测:真实后台 loop + StubSession,验证上下文注入/错误映射/串行。"""
import asyncio
import threading

import pytest

from app.mcp.client import (MCP_DISCONNECTED, MCP_START_FAILED, MCP_TIMEOUT,
                            MCP_TOOL_ERROR, McpClientManager, MCPError)


class StubSession:
    """伪 ClientSession:记录 call_tool 调用,返回固定响应或抛错。"""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _running_manager(session, timeout=15.0):
    """启动真实后台 loop 的 manager(测试用,不 spawn 子进程)。"""
    m = McpClientManager()
    m._session = session
    m._timeout = timeout
    m._loop = asyncio.new_event_loop()
    m._thread = threading.Thread(target=m._loop.run_forever, daemon=True)
    m._thread.start()
    return m


def _stop(m):
    if m._loop:
        m._loop.call_soon_threadsafe(m._loop.stop)
    if m._thread:
        m._thread.join(timeout=5)
        m._thread = None


def _text_result(payload: dict):
    class Block:
        type = "text"
        text = __import__("json").dumps(payload)
    class Result:
        content = [Block()]
    return Result()


def test_call_tool_passes_context_and_business():
    m = _running_manager(StubSession([_text_result({"success": True})]))
    try:
        out = m.call_tool("get_trace", incident_id=1, agent_run_id=2, trace_id="t1")
        assert m._session.calls == [("get_trace", {"incident_id": 1, "agent_run_id": 2,
                                                   "trace_id": "t1"})]
        assert out["success"] is True
        assert out["mcp_invocation_id"].startswith("mcp-")
    finally:
        _stop(m)


def test_call_tool_timeout_maps_error(monkeypatch):
    class Slow:
        async def call_tool(self, name, arguments):
            await asyncio.sleep(10)
    m = _running_manager(Slow(), timeout=0.05)
    # 模拟重启成功但重试仍超时:最终应映射为 MCP_TIMEOUT(不触发真实子进程重启)
    monkeypatch.setattr(McpClientManager, "_restart_session",
                        lambda self, timeout=10.0: True)
    try:
        with pytest.raises(MCPError) as ei:
            m.call_tool("get_trace", incident_id=1, agent_run_id=1, trace_id="t")
        assert ei.value.code == MCP_TIMEOUT
    finally:
        _stop(m)


def test_call_tool_tool_error_preserves_business_code():
    m = _running_manager(StubSession([_text_result(
        {"success": False, "error_code": "TRACE_NOT_FOUND"})]))
    try:
        out = m.call_tool("get_trace", incident_id=1, agent_run_id=1, trace_id="t")
        assert out["error_code"] == "TRACE_NOT_FOUND"   # 业务错误码透传,非 MCP_*
    finally:
        _stop(m)


def test_call_tool_not_ready_raises_disconnected():
    m = McpClientManager()
    with pytest.raises(MCPError) as ei:
        m.call_tool("get_trace", incident_id=1, agent_run_id=1, trace_id="t")
    assert ei.value.code in (MCP_DISCONNECTED, MCP_START_FAILED)


def test_call_tool_restart_once_on_disconnect(monkeypatch):
    """断线后重启会话并重试一次(同一调用),不降级 direct。"""
    class Flaky:
        def __init__(self):
            self.calls = 0

        async def call_tool(self, name, arguments):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("disconnected")
            return _text_result({"success": True})

    m = _running_manager(Flaky())

    async def fake_restart():
        m.is_ready = True
        # 重启后 session 保持(Flaky 第二次调用成功)
        m._session.calls = m._session.calls  # 保持同一 session,第二次成功

    monkeypatch.setattr(m, "_restart_async", fake_restart)
    try:
        out = m.call_tool("get_trace", incident_id=1, agent_run_id=1, trace_id="t")
        assert out["success"] is True
        assert m._session.calls == 2      # 首次失败 + 重启后重试一次
    finally:
        _stop(m)


def test_call_tool_restart_failure_raises_disconnected(monkeypatch):
    """重启也失败 → 明确 MCP 错误,不降级 direct。"""
    class Dead:
        async def call_tool(self, name, arguments):
            raise ConnectionError("disconnected")

    m = _running_manager(Dead())

    async def fake_restart():
        raise RuntimeError("cannot restart")

    monkeypatch.setattr(m, "_restart_async", fake_restart)
    try:
        with pytest.raises(MCPError) as ei:
            m.call_tool("get_trace", incident_id=1, agent_run_id=1, trace_id="t")
        assert ei.value.code == MCP_DISCONNECTED
    finally:
        _stop(m)
