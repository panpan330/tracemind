"""回归报告元数据采集(只读,不修改仓库)。"""
import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, cwd=ROOT).stdout.strip()
    except Exception:
        return "unknown"


def git_dirty() -> bool:
    try:
        out = subprocess.run(["git", "status", "--porcelain"],
                             capture_output=True, text=True, cwd=ROOT).stdout.strip()
        return bool(out)
    except Exception:
        return True


def fixture_hash() -> str:
    h = hashlib.sha256()
    for p in sorted((ROOT / "data/eval_cases").glob("*.json")):
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


def collect_metadata() -> dict:
    return {
        "git_commit": git_commit(), "git_dirty": git_dirty(),
        "dataset_version": "v1.4.0", "fixture_hash": fixture_hash(),
        "mcp_contract_version": "2.1.0", "diagnostic_policy_version": "1.0",
        "scenario_versions": {"SCN-001": "1.0", "SCN-002": "1.0"},
        "prompt_version": "v14", "model_sampling": {"temperature": 0.0, "top_p": 1.0},
        # V1.4 观测元数据(缺失用 n/a;prometheus/jaeger 相关版本来自 observability/ 与 Dockerfile)
        "metrics_backend": _env_or("TRACEMIND_METRICS_BACKEND", "n/a"),
        "trace_backend": _env_or("TRACEMIND_TRACE_BACKEND", "n/a"),
        "normalization_rule_version": "TRACE_NORMALIZER_V1",
        "otel_java_agent_version": _agent_version(),
        "otel_collector_version": _compose_image_version("otel-collector"),
        "jaeger_version": _compose_image_version("jaeger"),
        "prometheus_version": _compose_image_version("prometheus"),
        "grafana_version": _compose_image_version("grafana"),
    }


def _env_or(key: str, default: str) -> str:
    import os
    return os.environ.get(key, default)


def _compose_image_version(service: str) -> str:
    import re
    text = (ROOT / "compose.yml").read_text(encoding="utf-8")
    m = re.search(rf"^  {re.escape(service)}:\s*\n\s+image: ([\w./:-]+)", text, re.M)
    return m.group(1) if m else "n/a"


def _agent_version() -> str:
    text = (ROOT / "java/order-service/Dockerfile").read_text(encoding="utf-8")
    m = __import__("re").search(r"OTEL_JAVA_AGENT_VERSION=([\d.]+)", text)
    return m.group(1) if m else "n/a"
