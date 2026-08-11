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
        "dataset_version": "v1.3.0", "fixture_hash": fixture_hash(),
        "mcp_contract_version": "2.0.0", "diagnostic_policy_version": "1.0",
        "scenario_versions": {"SCN-001": "1.0", "SCN-002": "1.0"},
        "prompt_version": "v13", "model_sampling": {"temperature": 0.0, "top_p": 1.0},
    }
