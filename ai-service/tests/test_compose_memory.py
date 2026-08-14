import yaml
from pathlib import Path


def _compose():
    root = Path(__file__).resolve().parents[2]
    return yaml.safe_load((root / "compose.yml").read_text(encoding="utf-8"))


def test_compose_has_qdrant_service():
    c = _compose()
    svc = c["services"].get("qdrant")
    assert svc is not None
    ports = svc.get("ports", [])
    assert any("6333" in str(p) for p in ports)


def test_compose_has_qdrant_volume():
    c = _compose()
    assert "qdrant-data" in c.get("volumes", {})


def test_ai_service_depends_on_qdrant():
    c = _compose()
    deps = c["services"]["ai-service"].get("depends_on", {})
    assert "qdrant" in deps
