"""MCP 工具契约:常量、LLM 侧裁剪 Schema、契约校验工具。
说明:MCP_TOOL_CONTRACT_VERSION 是应用级契约字段,MCP 协议不天然提供。"""
import hashlib
import json
from typing import Any

from app.tools.registry import TOOL_REGISTRY

MCP_TOOL_CONTRACT_VERSION = "1.0"
SERVER_NAME = "tracemind-tools"
SERVER_VERSION = "0.1.0"

# 调查工具名(MCP 暴露集合;execute_fix/verify_recovery 不在此列)
TOOL_NAMES = frozenset({
    "get_service_metrics", "get_trace", "list_expensive_query_digests",
    "get_query_plan", "get_index_info",
})

# 上下文字段:MCP Client 注入,不进入 LLM Schema 与 Fixture 哈希
_CONTEXT_FIELDS = frozenset({"incident_id", "agent_run_id"})


def llm_tool_schemas() -> list[dict[str, Any]]:
    """裁剪后的 LLM 侧工具 Schema:仅业务参数(隐藏 incident_id/agent_run_id)。"""
    schemas = []
    for name in sorted(TOOL_NAMES):
        spec = TOOL_REGISTRY[name]
        schema = spec.input_schema.model_json_schema()
        schema["properties"] = {k: v for k, v in schema.get("properties", {}).items()
                                if k not in _CONTEXT_FIELDS}
        schema.pop("required", None)  # required 由程序 resolve 兜底
        schemas.append({"type": "function",
                        "function": {"name": name, "description": "",
                                     "parameters": schema}})
    return schemas


def schema_sha256(schema: dict) -> str:
    """标准化 JSON Schema 的 SHA-256(契约校验用)。"""
    blob = json.dumps(schema, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class MCPContractError(Exception):
    pass


def verify_contract(server_info: dict, tools: list[dict]) -> None:
    """启动契约校验:serverInfo / 工具名称集合 / inputSchema SHA-256 / Contract Version。
    不一致抛 MCPContractError,ai-service 启动失败或 readiness=false。"""
    if server_info.get("name") != SERVER_NAME:
        raise MCPContractError(f"serverInfo.name 不一致: {server_info.get('name')}")
    if server_info.get("version") != SERVER_VERSION:
        raise MCPContractError(f"serverInfo.version 不一致: {server_info.get('version')}")
    names = {t.get("name") for t in tools}
    if names != set(TOOL_NAMES):
        raise MCPContractError(f"工具名称集合不一致: {sorted(names)} vs {sorted(TOOL_NAMES)}")
    expected = {s["function"]["name"]: s["function"]["parameters"]
                for s in llm_tool_schemas()}
    for t in tools:
        if t["name"] in ("execute_fix", "verify_recovery"):
            raise MCPContractError(f"控制节点不应出现在 MCP 工具集: {t['name']}")
        if t["name"] not in expected:
            continue
        if schema_sha256(t.get("inputSchema", {})) != schema_sha256(expected[t["name"]]):
            raise MCPContractError(f"inputSchema 漂移: {t['name']}")
