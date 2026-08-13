# ai-service/tests/test_compose_v17.py
from pathlib import Path
import yaml


def _compose() -> dict:
    return yaml.safe_load(Path("../compose.yml").read_text(encoding="utf-8"))


def test_compose_has_mcp_tools_service():
    c = _compose()
    assert "mcp-tools" in c["services"]


def test_compose_three_internal_networks():
    c = _compose()
    for net in ("agent-mcp-network", "control-data-network", "tool-observation-network"):
        assert c["networks"][net].get("internal") is True


def test_compose_llm_egress_only_ai():
    c = _compose()
    ai = c["services"]["ai-service"]["networks"]
    mt = c["services"]["mcp-tools"]["networks"]
    assert "llm-egress-network" in ai
    assert "llm-egress-network" not in mt


def test_mcp_tools_no_host_ports():
    c = _compose()
    mt = c["services"]["mcp-tools"]
    assert "ports" not in mt or not mt["ports"]


def test_ai_service_http_transport_config():
    c = _compose()
    env = c["services"]["ai-service"]["environment"]
    assert env.get("TRACEMIND_MCP_TRANSPORT") == "streamable_http"
    assert "http://mcp-tools:8001/mcp" in env.get("TRACEMIND_MCP_HTTP_URL", "")
