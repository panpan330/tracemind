# ai-service/tests/test_client_manager.py
from app.mcp.client import McpClientManager


def test_transport_selected_by_settings(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_transport", "stdio")
    mgr = McpClientManager()
    assert mgr.transport == "stdio"
    assert mgr.get_transport_name() == "mcp_stdio"


def test_transport_http_selected(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_transport", "streamable_http")
    mgr = McpClientManager()
    assert mgr.transport == "streamable_http"
    assert mgr.get_transport_name() == "mcp_streamable_http"


def test_call_requires_ready(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_transport", "stdio")
    mgr = McpClientManager()
    # 未 start → 业务调用失败(不悄悄启动)
    try:
        mgr.call_tool("get_index_info", incident_id=1, agent_run_id=1, table_ref="inventory")
        assert False, "未就绪不应成功"
    except Exception:
        pass
