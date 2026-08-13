"""ci_manifest generate/check 一致性;check 不修改文件内容。"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def run_ci(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "scripts/ci/ci_manifest.py", *args],
                          capture_output=True, text=True, cwd=REPO)


def test_generate_then_check_passes():
    g = run_ci(["generate"])
    assert g.returncode == 0, g.stderr
    c = run_ci(["check"])  # 非 strict:内容一致即过(未提交改动仅 WARN)
    assert c.returncode == 0, c.stderr


def test_check_does_not_modify_file_content():
    contracts = REPO / "evaluation" / "contracts"
    run_ci(["generate"])
    before = {p.name: p.read_bytes() for p in contracts.glob("*.json")}
    c = run_ci(["check"])
    assert c.returncode == 0, c.stderr
    after = {p.name: p.read_bytes() for p in contracts.glob("*.json")}
    assert before == after  # check 执行前后内容一致(不写入)


def test_manifest_has_expected_versions():
    import json
    mcp = json.loads((REPO / "evaluation" / "contracts" / "mcp-tool-contract.json").read_text(encoding="utf-8"))
    assert mcp["contractVersion"] == "2.1.0"
    assert len(mcp["toolsSchemaHash"]) == 64  # sha256 hex
