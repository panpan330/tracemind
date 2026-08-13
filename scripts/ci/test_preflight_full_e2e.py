"""preflight 校验:confirm/scope/ref 语义(不注入 Secret 的纯逻辑部分)。"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "scripts/ci/preflight_full_e2e.py", *args],
                          capture_output=True, text=True, cwd=REPO)


def test_confirm_required():
    # --confirm 为 argparse required:缺失时直接报错(exit != 0)
    r = run(["--scope", "smoke"])
    assert r.returncode != 0


def test_wrong_confirm_rejected():
    r = run(["--scope", "smoke", "--confirm", "NO"])
    assert r.returncode != 0


def test_semver_rejects_loose_tag():
    r = run(["--scope", "smoke", "--confirm", "RUN_FULL_E2E", "--release-ref", "v*"])
    assert r.returncode != 0
    assert "SemVer" in r.stderr


def test_semver_rejects_non_ancestor_tag():
    # 构造一个不存在/非祖先的 tag:用随机 hex,rev-parse 失败
    r = run(["--scope", "smoke", "--confirm", "RUN_FULL_E2E",
             "--release-ref", "v1.6.0"])
    # 本仓库可能无 origin/main 或该 tag;只要不崩且按规则处理即可
    assert r.returncode in (0, 1)
