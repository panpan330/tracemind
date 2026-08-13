"""MCP 相关 Settings(按进程拆分;模块 import 不实例化,入口显式构建)。"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class _McpBase(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRACEMIND_", extra="ignore")
    mcp_transport: str = "stdio"          # stdio | streamable_http


class McpClientSettings(_McpBase):
    mcp_http_url: str = ""
    mcp_http_bearer_token: str = ""
    mcp_http_connect_timeout_seconds: float = 5.0
    mcp_http_request_timeout_seconds: float = 30.0
    mcp_http_max_retries: int = 3

    def validate_runtime(self) -> bool:
        if self.mcp_transport == "streamable_http":
            return bool(self.mcp_http_url and self.mcp_http_bearer_token)
        return True   # stdio 不需 URL/Token


class McpHttpServerSettings(_McpBase):
    mcp_http_url: str = "http://0.0.0.0:8001/mcp"
    mcp_auth_clients_file: str = ""
    mcp_max_request_bytes: int = 262144
    mcp_max_result_bytes: int = 1048576
    mcp_audit_db_url: str = ""
    mcp_protocol_required: str = ""

    def validate_runtime(self) -> bool:
        if self.mcp_transport == "streamable_http":
            return bool(self.mcp_auth_clients_file)
        return True


class McpStdioServerSettings(_McpBase):
    pass


def build_mcp_server_settings() -> McpHttpServerSettings:
    """MCP HTTP Server 进程入口显式构建(不实例化 AI 字段)。"""
    return McpHttpServerSettings()
