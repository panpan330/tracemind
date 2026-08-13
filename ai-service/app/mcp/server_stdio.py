"""stdio MCP Server 入口(本地开发/离线评测 fixture)。

用法:python -m app.mcp.server_stdio [--fixture-file <case.json>]
"""
import argparse
import logging
import sys

from app.mcp.server_factory import create_mcp_server


def load_fixture(fixture_file: str) -> dict:
    from app.config import settings
    if not settings.eval_mode:
        raise SystemExit("--fixture-file 仅允许 TRACEMIND_EVAL_MODE=true")
    import json
    from pathlib import Path
    base = Path(settings.eval_fixture_dir or ".").resolve()
    p = (base / fixture_file).resolve()
    if not p.is_relative_to(base):
        raise SystemExit("fixture 文件必须位于评测目录")
    payload = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "tool_fixtures" in payload:
        payload = payload["tool_fixtures"]
    return payload


def main() -> None:
    import app.tools  # noqa: F401  注册 TOOL_REGISTRY(工具 schema 与 handler 关联)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-file", default=None)
    args = parser.parse_args()
    fixture = load_fixture(args.fixture_file) if args.fixture_file else None
    if fixture is not None:
        logging.info("fixture 已加载: %s(%d 条)", args.fixture_file, len(fixture))
    mcp = create_mcp_server(runtime="fixture" if fixture else "stdio", fixture=fixture)
    mcp.run()   # stdio transport;stdout 仅 MCP JSON-RPC


if __name__ == "__main__":
    main()
