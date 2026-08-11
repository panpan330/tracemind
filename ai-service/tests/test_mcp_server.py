"""MCP Server 单元测试:工具注册/委托/Fixture 加载/上下文校验。"""
import json

from app.mcp.contract import (MCP_TOOL_CONTRACT_VERSION, SERVER_NAME, TOOL_NAMES,
                              llm_tool_schemas, schema_sha256)
from app.mcp.server import run_server


class FakeFastMCP:
    """替代 mcp.server.fastmcp.FastMCP:记录工具注册与 run,不真正启动。"""
    def __init__(self, name, version=None):
        self.name = name
        self.version = version
        self._tools = []
        self._ran = False

    def tool(self, fn=None, **kwargs):
        if fn is None:
            return lambda f: (self._tools.append(f) or f)
        self._tools.append(fn)
        return fn

    def run(self):
        self._ran = True


def test_contract_constants():
    assert MCP_TOOL_CONTRACT_VERSION == "2.0.0"
    assert SERVER_NAME == "tracemind-tools"
    assert TOOL_NAMES == frozenset({
        "get_service_metrics", "get_trace", "list_expensive_query_digests",
        "get_query_plan", "get_index_info"})


def test_llm_tool_schemas_hide_context_fields():
    schemas = llm_tool_schemas()
    assert len(schemas) == 7
    for s in schemas:
        props = s["function"]["parameters"]["properties"]
        assert "incident_id" not in props and "agent_run_id" not in props


def test_schema_sha256_stable():
    s1 = {"type": "object", "properties": {"a": {"type": "string"}}}
    assert schema_sha256(s1) == schema_sha256(dict(s1))
    assert len(schema_sha256(s1)) == 64


def test_server_registers_seven_tools_and_fixture(monkeypatch, tmp_path):
    from app.mcp import server as server_mod
    from app.tools import execute

    fake = {"get_service_metrics:abc": {"ok": True, "data": {"p95Ms": 100}}}
    (tmp_path / "case.json").write_text(json.dumps(fake), encoding="utf-8")

    loaded = {}
    from app.config import settings
    monkeypatch.setattr("app.mcp.server.set_eval_fixture", lambda f: loaded.update(f))
    monkeypatch.setattr("mcp.server.fastmcp.FastMCP", FakeFastMCP)
    monkeypatch.setattr(settings, "eval_mode", True)
    monkeypatch.setattr(settings, "eval_fixture_dir", str(tmp_path))

    run_server(fixture_file="case.json")   # fixture 文件位于评测目录(相对名)

    fake_mcp = FakeFastMCP._last_created
    assert fake_mcp.name == SERVER_NAME
    assert len(fake_mcp._tools) == 7
    assert fake_mcp._ran is True
    assert loaded == fake


# 记录最后创建的 FakeFastMCP 供断言
FakeFastMCP._last_created = None
_orig_init = FakeFastMCP.__init__


def _init_with_record(self, name, version=None):
    _orig_init(self, name, version)
    FakeFastMCP._last_created = self


FakeFastMCP.__init__ = _init_with_record
