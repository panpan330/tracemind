# ai-service/tests/test_config_split.py
import os

from app.config.mcp import McpClientSettings, McpHttpServerSettings


def _clean_env():
    os.environ.pop("TRACEMIND_MCP_TRANSPORT", None)
    os.environ.pop("TRACEMIND_MCP_AUTH_CLIENTS_FILE", None)
    os.environ.pop("TRACEMIND_MCP_HTTP_BEARER_TOKEN", None)
    os.environ.pop("TRACEMIND_MCP_HTTP_URL", None)


def test_mcp_http_server_fail_closed_without_clients_file():
    _clean_env()
    os.environ["TRACEMIND_MCP_TRANSPORT"] = "streamable_http"
    try:
        assert McpHttpServerSettings().validate_runtime() is False
    finally:
        _clean_env()


def test_mcp_client_fail_closed_without_token():
    _clean_env()
    os.environ["TRACEMIND_MCP_TRANSPORT"] = "streamable_http"
    try:
        assert McpClientSettings().validate_runtime() is False
    finally:
        _clean_env()


def test_mcp_client_ok_with_creds():
    _clean_env()
    os.environ["TRACEMIND_MCP_TRANSPORT"] = "streamable_http"
    os.environ["TRACEMIND_MCP_HTTP_URL"] = "http://mcp-tools:8001/mcp"
    os.environ["TRACEMIND_MCP_HTTP_BEARER_TOKEN"] = "test-token"
    try:
        assert McpClientSettings().validate_runtime() is True
    finally:
        _clean_env()


def test_mcp_stdio_ok_without_creds():
    _clean_env()
    os.environ["TRACEMIND_MCP_TRANSPORT"] = "stdio"
    try:
        assert McpClientSettings().validate_runtime() is True
        assert McpHttpServerSettings().validate_runtime() is True
    finally:
        _clean_env()


def test_common_settings_legacy_still_works():
    from app.config import settings as legacy
    assert hasattr(legacy, "llm_mode")   # 既有全局配置兼容


def test_build_mcp_server_settings():
    from app.config.mcp import build_mcp_server_settings
    s = build_mcp_server_settings()
    assert s is not None and hasattr(s, "mcp_auth_clients_file")
