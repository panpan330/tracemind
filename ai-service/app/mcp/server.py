"""MCP Server:五个只读调查工具(stdout 纯净,日志走 stderr)。"""
import argparse
import logging
import sys

from app.tools.execute import execute_tool, set_eval_fixture
from app.mcp.contract import SERVER_NAME, SERVER_VERSION

logger = logging.getLogger("app.mcp.server")
logging.basicConfig(stream=sys.stderr, level=logging.INFO)


def run_server(fixture_file: str | None = None) -> None:
    """构造并注册 FastMCP;fixture_file 非空时进程内加载 fixture(仅 EVAL_MODE)。"""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(SERVER_NAME, version=SERVER_VERSION)

    if fixture_file:
        from app.config import settings
        if not settings.eval_mode:
            raise SystemExit("--fixture-file 仅允许 TRACEMIND_EVAL_MODE=true")
        import json
        from pathlib import Path
        base = Path(settings.eval_fixture_dir or ".").resolve()
        fixture_path = (base / fixture_file).resolve()
        if not fixture_path.is_relative_to(base):
            raise SystemExit("fixture 文件必须位于评测目录")
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        set_eval_fixture(payload)
        logger.info("fixture 已加载: %s(%d 条)", fixture_file, len(payload))

    def _delegate(name: str, incident_id: int, agent_run_id: int, **business):
        # Fixture 模式 synthetic context:不校验 Incident/AgentRun 存在
        return execute_tool(name, incident_id=incident_id, agent_run_id=agent_run_id,
                            transport="mcp_stdio", **business)

    @mcp.tool()
    def get_service_metrics(incident_id: int, agent_run_id: int,
                            service_ref: str, window_seconds: int) -> dict:
        return _delegate("get_service_metrics", incident_id, agent_run_id,
                         service_ref=service_ref, window_seconds=window_seconds)

    @mcp.tool()
    def get_trace(incident_id: int, agent_run_id: int, trace_id: str) -> dict:
        return _delegate("get_trace", incident_id, agent_run_id, trace_id=trace_id)

    @mcp.tool()
    def list_expensive_query_digests(incident_id: int, agent_run_id: int,
                                     window_seconds: int) -> dict:
        return _delegate("list_expensive_query_digests", incident_id, agent_run_id,
                         window_seconds=window_seconds)

    @mcp.tool()
    def get_query_plan(incident_id: int, agent_run_id: int, query_ref: str,
                       sample_parameters: dict) -> dict:
        return _delegate("get_query_plan", incident_id, agent_run_id,
                         query_ref=query_ref, sample_parameters=sample_parameters)

    @mcp.tool()
    def get_index_info(incident_id: int, agent_run_id: int, table_ref: str) -> dict:
        return _delegate("get_index_info", incident_id, agent_run_id, table_ref=table_ref)

    mcp.run()   # stdio transport;stdout 仅 MCP JSON-RPC


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-file", default=None)
    args = parser.parse_args()
    run_server(args.fixture_file)


if __name__ == "__main__":
    main()
