"""契约与评测 Manifest 的 generate/check 双模式。
用法:
  python scripts/ci/ci_manifest.py generate   # 开发者显式更新(更新 evaluation/contracts/)
  python scripts/ci/ci_manifest.py check      # CI 只校验,禁止修改文件

check 语义:根据当前源码计算 → 与已提交 Manifest 比较;不一致或版本未同步 → 失败;
工作树被修改 → 失败。Canonical JSON(稳定排序)避免无意义 diff。
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONTRACTS = REPO / "evaluation" / "contracts"
AI = REPO / "ai-service"


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _extract_const(path: Path, name: str) -> str:
    """从源码文件解析常量:支持 'NAME = "value"' 与 '"NAME": "value"' 两种形式(不经 import)。"""
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith(name + " =") or s.startswith(name + "="):
            return s.split("=", 1)[1].strip().strip('"').strip("'")
        if s.startswith(f'"{name}"'):
            # '"name": "value"}' 等字典形式,取引号内值
            val = s.split(":", 1)[1].strip()
            if val.startswith('"'):
                val = val.split('"')[1]
            return val
    return ""


def _compute_all() -> dict:
    mcp_path = AI / "app" / "mcp" / "contract.py"
    replay_path = AI / "app" / "replay" / "versions.py"
    replay_api_path = AI / "app" / "api" / "replay.py"
    tools_path = AI / "app" / "tools" / "schemas.py"
    policy_path = REPO / "data" / "evaluation_policy.yaml"

    mcp_ver = _extract_const(mcp_path, "MCP_TOOL_CONTRACT_VERSION")
    policy_ver = _extract_const(replay_path, "POLICY_BUNDLE_VERSION")
    replay_schema = _extract_const(replay_path, "REPLAY_SCHEMA_VERSION")
    response_schema = _extract_const(replay_api_path, "responseSchemaVersion")
    playback = _extract_const(replay_path, "PLAYBACK_POLICY_VERSION")

    return {
        "mcp": {
            "contractVersion": mcp_ver,
            "toolsSchemaHash": sha256_bytes(tools_path.read_bytes()),
        },
        "policy": {
            "bundleVersion": policy_ver,
            "policyFileHash": sha256_bytes(policy_path.read_bytes()),
        },
        "replay": {
            "schemaVersion": replay_schema,
            "responseSchemaVersion": response_schema,
            "playbackPolicyVersion": playback,
            "sourceHash": sha256_bytes(replay_path.read_bytes()),
        },
    }


FILES = [
    ("mcp", "mcp-tool-contract.json"),
    ("policy", "diagnostic-policy-manifest.json"),
    ("replay", "replay-schema-manifest.json"),
]


def _git_status_clean() -> bool:
    import subprocess
    r = subprocess.run(["git", "status", "--porcelain", "--", "evaluation/contracts/"],
                       capture_output=True, text=True, cwd=REPO)
    return r.returncode == 0 and not r.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["generate", "check"])
    ap.add_argument("--strict-git", action="store_true",
                    help="check 时额外要求 contracts 工作树与 git HEAD 一致(CI 用)")
    args = ap.parse_args()
    current = _compute_all()

    if args.cmd == "generate":
        CONTRACTS.mkdir(parents=True, exist_ok=True)
        for key, fname in FILES:
            (CONTRACTS / fname).write_text(canonical(current[key]), encoding="utf-8")
        print("generate OK → evaluation/contracts/")
        return 0

    # check
    fail = False
    for key, fname in FILES:
        f = CONTRACTS / fname
        if not f.exists():
            print(f"FAIL: {fname} 缺失,需先 generate", file=sys.stderr)
            fail = True
            continue
        committed = json.loads(f.read_text(encoding="utf-8"))
        if committed != current[key]:
            print(f"FAIL: {fname} 与源码不一致,需 generate", file=sys.stderr)
            fail = True
        else:
            print(f"OK: {fname}")
    if not _git_status_clean():
        if args.strict_git:
            print("FAIL: evaluation/contracts/ 工作树被修改(check 不得写文件)", file=sys.stderr)
            fail = True
        else:
            print("WARN: evaluation/contracts/ 有未提交改动(本地开发正常;CI 用 --strict-git)")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
