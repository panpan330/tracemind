# ai-service/tests/test_dockerfile_targets.py
from pathlib import Path


def test_dockerfile_has_both_targets():
    df = Path("Dockerfile").read_text(encoding="utf-8")
    assert "AS ai-runtime" in df
    assert "AS mcp-tools-runtime" in df


def test_mcp_tools_target_skips_llm_deps():
    df = Path("Dockerfile").read_text(encoding="utf-8")
    seg = df.split("AS mcp-tools-runtime")[1]
    assert "langgraph" not in seg
    assert "langchain" not in seg


def test_mcp_tools_target_starts_server_http():
    df = Path("Dockerfile").read_text(encoding="utf-8")
    seg = df.split("AS mcp-tools-runtime")[1]
    assert "app.mcp.server_http" in seg
