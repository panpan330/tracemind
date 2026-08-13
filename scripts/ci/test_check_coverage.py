"""check_coverage 单元测试:低于基线失败;达到或超过通过。"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(current: float, base_data: dict, lang="python", metric="line") -> subprocess.CompletedProcess:
    # 脚本绝对路径(测试可能从任意 cwd 运行,如 scripts/ci 下 pytest)
    script = Path(__file__).resolve().parent / "check_coverage.py"
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "coverage.json"
        base.write_text(json.dumps(base_data), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(script),
             "--lang", lang, "--metric", metric, "--current", str(current),
             "--base-file", str(base)],
            capture_output=True, text=True)


def test_below_threshold_fails():
    r = _run(78.4, {"python": {"line": 78.51, "branch": 0.0}})
    assert r.returncode == 1
    assert "FAIL" in r.stderr


def test_at_threshold_passes():
    r = _run(78.51, {"python": {"line": 78.51, "branch": 0.0}})
    assert r.returncode == 0


def test_above_threshold_passes():
    r = _run(80.0, {"python": {"line": 78.51, "branch": 0.0}})
    assert r.returncode == 0


def test_web_branch():
    r = _run(71.74, {"web": {"line": 82.25, "branch": 71.74}}, lang="web", metric="branch")
    assert r.returncode == 0
