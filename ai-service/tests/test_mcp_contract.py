"""契约校验:serverInfo/名称集合/inputSchema 签名/Contract Version。"""
import pytest

from app.mcp.contract import (MCP_TOOL_CONTRACT_VERSION, SERVER_NAME, TOOL_NAMES,
                              MCPContractError, verify_contract, mcp_tool_schemas)


def _tools_from_schemas():
    return [{"name": name, "inputSchema": schema}
            for name, schema in mcp_tool_schemas().items()]


def test_verify_contract_ok():
    tools = _tools_from_schemas()
    verify_contract({"name": SERVER_NAME, "version": "0.1.0"}, tools)


def test_verify_contract_name_mismatch():
    tools = _tools_from_schemas()
    with pytest.raises(MCPContractError):
        verify_contract({"name": "other", "version": "0.1.0"}, tools)


def test_verify_contract_tool_set_mismatch():
    tools = _tools_from_schemas()
    tools = [t for t in tools if t["name"] != "get_trace"]
    with pytest.raises(MCPContractError):
        verify_contract({"name": SERVER_NAME, "version": "0.1.0"}, tools)


def test_verify_contract_schema_drift():
    tools = _tools_from_schemas()
    # 属性名/类型漂移 → 不一致
    tools[0]["inputSchema"]["properties"]["bogus_field"] = {"type": "string"}
    with pytest.raises(MCPContractError):
        verify_contract({"name": SERVER_NAME, "version": "0.1.0"}, tools)


def test_verify_contract_accepts_constraint_differences():
    """FastMCP 从类型注解生成 schema,不携带 pydantic 约束(pattern/minimum);
    契约校验只比对属性名+类型+required,忽略 title/约束。"""
    tools = _tools_from_schemas()
    # get_service_metrics 含 pattern/minimum/maximum 约束;去掉约束后签名应仍一致
    gsm = next(t for t in tools if t["name"] == "get_service_metrics")
    gsm["inputSchema"]["properties"]["service_ref"] = {"type": "string"}
    gsm["inputSchema"]["properties"]["window_seconds"] = {"type": "integer"}
    verify_contract({"name": SERVER_NAME, "version": "0.1.0"}, tools)


def test_verify_contract_rejects_control_tools():
    tools = _tools_from_schemas()
    tools.append({"name": "execute_fix", "inputSchema": {"type": "object"}})
    with pytest.raises(MCPContractError):
        verify_contract({"name": SERVER_NAME, "version": "0.1.0"}, tools)
