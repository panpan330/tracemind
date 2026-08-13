"""execute_tool 薄封装:委托给 tools_core.ToolExecutionService(保持既有调用方兼容)。"""
from app.tools_core.context import ClientInvocationContext
from app.tools_core.service import ToolExecutionService

_service = ToolExecutionService(ports={}, runtime="real")


def set_eval_fixture(fixture: dict | None) -> None:
    """离线评测注入(仅 fixture runtime 允许;主进程 real runtime 下调用会抛 FIXTURE_FORBIDDEN)。"""
    _service.set_fixture(fixture)


def execute_tool(tool_name: str, incident_id: int | None = None,
                 agent_run_id: int | None = None, transport: str = "legacy_direct",
                 mcp_invocation_id: str | None = None,
                 mcp_attempt: int | None = None, **kwargs) -> dict:
    """统一工具执行入口(兼容 V1.0-V1.6 签名):委托 ToolExecutionService。"""
    ctx = ClientInvocationContext(incident_id=incident_id or 0,
                                  agent_run_id=agent_run_id or 0,
                                  tool_call_id=f"legacy-{mcp_invocation_id or 'direct'}",
                                  purpose="investigation")
    return _service.execute(tool_name, kwargs, ctx, transport=transport,
                            mcp_invocation_id=mcp_invocation_id, mcp_attempt=mcp_attempt)
