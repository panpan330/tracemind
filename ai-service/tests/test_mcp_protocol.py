"""MCP 协议集成:真实 stdio 子进程,验证 initialize/tools/list/call。"""
import asyncio
import json
import sys

import pytest

from app.mcp.contract import SERVER_NAME, SERVER_VERSION, TOOL_NAMES


@pytest.mark.asyncio
async def test_stdio_initialize_list_call(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    args = {"service_ref": "inventory-service", "window_seconds": 300}
    key = "get_service_metrics:" + __import__("hashlib").sha256(
        json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    fixture = {key: {"ok": True, "data": {"p95Ms": 120, "representativeSlowTraceId": "t1"}}}
    (tmp_path / "case.json").write_text(json.dumps(fixture), encoding="utf-8")

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.server", "--fixture-file", str(tmp_path / "case.json")],
        env={**__import__("os").environ, "TRACEMIND_EVAL_MODE": "true",
             "TRACEMIND_EVAL_FIXTURE_DIR": str(tmp_path)})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.serverInfo.name == SERVER_NAME
            assert init.serverInfo.version == SERVER_VERSION
            tools = (await session.list_tools()).tools
            assert {t.name for t in tools} == set(TOOL_NAMES)
            assert "execute_fix" not in {t.name for t in tools}
            res = await session.call_tool(
                "get_service_metrics",
                {"incident_id": 1, "agent_run_id": 1,
                 "service_ref": "inventory-service", "window_seconds": 300})
            text = "".join(c.text for c in res.content if c.type == "text")
            assert json.loads(text)["success"] is True


@pytest.mark.asyncio
async def test_stdio_fixture_gate_requires_eval_mode(tmp_path):
    """--fixture-file 仅 EVAL_MODE=true 允许;否则子进程退出。"""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    (tmp_path / "case.json").write_text("{}", encoding="utf-8")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.server", "--fixture-file", str(tmp_path / "case.json")],
        env={**__import__("os").environ, "TRACEMIND_EVAL_MODE": "false"})
    with pytest.raises(Exception):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=10)
