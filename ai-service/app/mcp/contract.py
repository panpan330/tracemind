"""MCP 工具契约:常量、LLM 侧裁剪 Schema、契约校验工具。
说明:MCP_TOOL_CONTRACT_VERSION 是应用级契约字段,MCP 协议不天然提供。"""
import hashlib
import json
from typing import Any

from app.tools.registry import TOOL_REGISTRY

MCP_TOOL_CONTRACT_VERSION = "2.0.0"
SERVER_NAME = "tracemind-tools"
SERVER_VERSION = "0.2.0"

# 调查工具名(MCP 暴露集合;execute_fix/verify_recovery 不在此列)
TOOL_NAMES = frozenset({
    "get_service_metrics", "get_trace", "list_expensive_query_digests",
    "get_query_plan", "get_index_info", "get_lock_waiters",
    "get_transaction_details",
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


def mcp_tool_schemas() -> dict[str, dict[str, Any]]:
    """MCP Server 应暴露的完整工具 Schema(含 incident_id/agent_run_id 上下文)。
    契约校验的本地预期源(属性名 + 类型 + required)。"""
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(TOOL_NAMES):
        spec = TOOL_REGISTRY[name]
        schema = spec.input_schema.model_json_schema()
        props: dict[str, Any] = {"incident_id": {"type": "integer"},
                                 "agent_run_id": {"type": "integer"}}
        props.update(schema.get("properties", {}))
        if name == "list_expensive_query_digests":
            # 兼容参数(调用方传,工具内部用默认窗口),可选不入 required
            props["window_seconds"] = {"type": "integer"}
        required = ["incident_id", "agent_run_id"] + list(schema.get("required", []))
        out[name] = {"properties": props, "required": required}
    return out


def _signature(schema: dict) -> dict:
    """归一化签名:属性名→类型映射 + required 集合(忽略 title/枚举/边界;
    兼容 anyOf 可选参数,取第一个非 null 类型)。"""
    def sig_type(v: Any) -> str | None:
        if not isinstance(v, dict):
            return None
        if "type" in v:
            return v["type"]
        for item in v.get("anyOf", []):
            if isinstance(item, dict) and item.get("type") != "null":
                return item.get("type")
        return None

    props = {k: sig_type(v) for k, v in schema.get("properties", {}).items()}
    return {"props": props, "required": sorted(set(schema.get("required", [])))}


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
    expected = mcp_tool_schemas()
    for t in tools:
        if t["name"] in ("execute_fix", "verify_recovery"):
            raise MCPContractError(f"控制节点不应出现在 MCP 工具集: {t['name']}")
        if t["name"] not in expected:
            continue
        if _signature(t.get("inputSchema", {})) != _signature(expected[t["name"]]):
            raise MCPContractError(f"inputSchema 不一致: {t['name']}")
