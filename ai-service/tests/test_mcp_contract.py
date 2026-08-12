"""契约校验:serverInfo/名称集合/inputSchema 签名/Contract Version。"""
import pytest

from app.mcp.contract import (MCP_TOOL_CONTRACT_VERSION, SERVER_NAME, TOOL_NAMES,
                              MCPContractError, verify_contract, mcp_tool_schemas,
                              llm_tool_schemas)


def _tools_from_schemas():
    return [{"name": name, "inputSchema": schema}
            for name, schema in mcp_tool_schemas().items()]


def test_verify_contract_ok():
    tools = _tools_from_schemas()
    verify_contract({"name": SERVER_NAME, "version": "0.3.0"}, tools)


def test_verify_contract_name_mismatch():
    tools = _tools_from_schemas()
    with pytest.raises(MCPContractError):
        verify_contract({"name": "other", "version": "0.3.0"}, tools)


def test_verify_contract_tool_set_mismatch():
    tools = _tools_from_schemas()
    tools = [t for t in tools if t["name"] != "get_trace"]
    with pytest.raises(MCPContractError):
        verify_contract({"name": SERVER_NAME, "version": "0.3.0"}, tools)


def test_verify_contract_schema_drift():
    tools = _tools_from_schemas()
    # 属性名/类型漂移 → 不一致
    tools[0]["inputSchema"]["properties"]["bogus_field"] = {"type": "string"}
    with pytest.raises(MCPContractError):
        verify_contract({"name": SERVER_NAME, "version": "0.3.0"}, tools)


def test_verify_contract_accepts_constraint_differences():
    """FastMCP 从类型注解生成 schema,不携带 pydantic 约束(pattern/minimum);
    契约校验只比对属性名+类型+required,忽略 title/约束。"""
    tools = _tools_from_schemas()
    # get_service_metrics 含 pattern/minimum/maximum 约束;去掉约束后签名应仍一致
    gsm = next(t for t in tools if t["name"] == "get_service_metrics")
    gsm["inputSchema"]["properties"]["service_ref"] = {"type": "string"}
    gsm["inputSchema"]["properties"]["window_seconds"] = {"type": "integer"}
    verify_contract({"name": SERVER_NAME, "version": "0.3.0"}, tools)


def test_verify_contract_rejects_control_tools():
    tools = _tools_from_schemas()
    tools.append({"name": "execute_fix", "inputSchema": {"type": "object"}})
    with pytest.raises(MCPContractError):
        verify_contract({"name": SERVER_NAME, "version": "0.3.0"}, tools)


# ---- V1.3:契约 2.0.0,工具集 5→7 ----

def test_contract_version_200():
    assert MCP_TOOL_CONTRACT_VERSION == "2.1.0"


def test_seven_tools_in_contract():
    assert TOOL_NAMES == {
        "get_service_metrics", "get_trace", "list_expensive_query_digests",
        "get_query_plan", "get_index_info", "get_lock_waiters",
        "get_transaction_details",
    }


def test_mcp_schemas_include_context_and_lock_tools():
    schemas = mcp_tool_schemas()
    assert "get_lock_waiters" in schemas and "get_transaction_details" in schemas
    lw = schemas["get_lock_waiters"]
    assert "incident_id" in lw["required"] and "agent_run_id" in lw["required"]
    td = schemas["get_transaction_details"]
    assert "transaction_ref" in td["properties"]


def test_llm_schemas_hide_context():
    schemas = llm_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == TOOL_NAMES
    for s in schemas:
        assert "incident_id" not in s["function"]["parameters"]["properties"]
