# ai-service/tests/test_tools_core_import_boundary.py
"""tools_core 禁止导入 AI 应用层黑名单:agent/langgraph/llm/prompt/fix_executor/
session_terminator/fastapi/fastmcp。用 AST 静态扫描,不依赖开发者自觉。"""
import ast
from pathlib import Path

CORE_DIR = Path("app/tools_core")
FORBIDDEN = {"agent", "langgraph", "llm", "prompt", "fix_executor",
             "session_terminator", "fastapi", "fastmcp"}
_ALLOWED_TOPS = {"app", "app.tools_core", "app.tools_core.errors", "app.tools_core.ports",
                 "app.tools_core.registry", "app.tools_core.schemas", "app.tools_core.handlers"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                tops.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                tops.add(node.module.split(".")[0])
    return tops


def test_tools_core_no_forbidden_imports():
    bad: list[str] = []
    for py in CORE_DIR.rglob("*.py"):
        hit = _imports(py) & FORBIDDEN
        if hit:
            bad.append(f"{py}: {sorted(hit)}")
    assert not bad, f"tools_core 违反导入边界: {bad}"


def test_handlers_only_import_ports_core():
    bad: list[str] = []
    for py in (CORE_DIR / "handlers").rglob("*.py"):
        tops = _imports(py)
        # handlers 只允许 tools_core 内部 + 标准库(标准库 top 如 typing/datetime/hashlib 放行)
        allowed = _ALLOWED_TOPS | {
            "typing", "datetime", "hashlib", "json", "time", "uuid", "anyio", "logging", "os",
        }
        diff = tops - allowed
        if diff:
            bad.append(f"{py}: {sorted(diff)}")
    assert not bad, f"handlers 不应导入: {bad}"
