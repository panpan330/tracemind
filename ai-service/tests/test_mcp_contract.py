"""契约校验:serverInfo/名称集合/inputSchema SHA-256/Contract Version。"""
import pytest

from app.mcp.contract import (MCP_TOOL_CONTRACT_VERSION, SERVER_NAME, TOOL_NAMES,
                              MCPContractError, verify_contract, llm_tool_schemas)


def _tools_from_schemas():
    return [{"name": s["function"]["name"],
             "inputSchema": s["function"]["parameters"]} for s in llm_tool_schemas()]


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
    tools[0]["inputSchema"]["properties"] = {"service_ref": {"type": "str"}}
    with pytest.raises(MCPContractError):
        verify_contract({"name": SERVER_NAME, "version": "0.1.0"}, tools)


def test_verify_contract_rejects_control_tools():
    tools = _tools_from_schemas()
    tools.append({"name": "execute_fix", "inputSchema": {"type": "object"}})
    with pytest.raises(MCPContractError):
        verify_contract({"name": SERVER_NAME, "version": "0.1.0"}, tools)
