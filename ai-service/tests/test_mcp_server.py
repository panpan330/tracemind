"""MCP Server 单元测试:工具注册/契约/Fixture 加载。"""
import json

from app.mcp.contract import (MCP_TOOL_CONTRACT_VERSION, SERVER_NAME, TOOL_NAMES,
                              llm_tool_schemas, schema_sha256)


class FakeFastMCP:
    """替代 mcp.server.fastmcp.FastMCP:记录工具注册与 run,不真正启动。"""
    _last_created = None

    def __init__(self, name, version=None):
        self.name = name
        self.version = version
        self._tools = []
        self._ran = False
        FakeFastMCP._last_created = self

    def tool(self, fn=None, **kwargs):
        if fn is None:
            return lambda f: (self._tools.append(f) or f)
        self._tools.append(fn)
        return fn

    def run(self):
        self._ran = True


def test_contract_constants():
    assert MCP_TOOL_CONTRACT_VERSION == "2.1.0"
    assert SERVER_NAME == "tracemind-tools"
    assert TOOL_NAMES == frozenset({
        "get_service_metrics", "get_trace", "list_expensive_query_digests",
        "get_query_plan", "get_index_info", "get_lock_waiters",
        "get_transaction_details"})


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


def test_factory_registers_seven_tools(monkeypatch):
    import app.tools  # noqa: F401  触发 TOOL_REGISTRY 注册
    from app.mcp.server_factory import create_mcp_server

    fake = {"get_service_metrics:abc": {"ok": True, "data": {"p95Ms": 100}}}
    monkeypatch.setattr("mcp.server.fastmcp.FastMCP", FakeFastMCP)

    mcp = create_mcp_server(runtime="fixture", fixture=fake)

    fake_mcp = FakeFastMCP._last_created
    assert fake_mcp.name == SERVER_NAME
    # 7 个只读调查工具,不含 execute_fix/verify_recovery
    names = {t.__name__ for t in fake_mcp._tools}
    assert names == set(TOOL_NAMES)
    assert "execute_fix" not in names and "verify_recovery" not in names


def test_stdio_entry_loads_fixture(monkeypatch, tmp_path):
    from app.mcp import server_stdio

    fake = {"get_service_metrics:abc": {"ok": True, "data": {"p95Ms": 100}}}
    (tmp_path / "case.json").write_text(json.dumps(fake), encoding="utf-8")

    from app.config import settings
    monkeypatch.setattr(settings, "eval_mode", True)
    monkeypatch.setattr(settings, "eval_fixture_dir", str(tmp_path))

    payload = server_stdio.load_fixture("case.json")
    assert payload == fake
