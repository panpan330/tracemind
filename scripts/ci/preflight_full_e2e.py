"""Full E2E preflight:confirm/scope/ref 校验 + 目标 SHA 解析(纯逻辑,无 Secret)。"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

SEMVER = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")


def run_git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=REPO)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", required=True, choices=["smoke", "release"])
    ap.add_argument("--confirm", required=True)
    ap.add_argument("--release-ref", default="")
    args = ap.parse_args()

    if args.confirm != "RUN_FULL_E2E":
        print("FATAL: confirm 必须为 RUN_FULL_E2E", file=sys.stderr)
        return 1

    if args.release_ref:
        if not SEMVER.match(args.release_ref):
            print(f"FATAL: release_ref {args.release_ref} 不满足 SemVer(vX.Y.Z)", file=sys.stderr)
            return 1
        r = run_git(["rev-parse", "--verify", f"{args.release_ref}^{{commit}}"])
        if r.returncode != 0:
            print(f"FATAL: 无法解析 {args.release_ref}(rev-parse 失败)", file=sys.stderr)
            return 1
        sha = r.stdout.strip()
        if not SHA40.match(sha):
            print(f"FATAL: rev-parse 输出非 40 位 SHA: {sha}", file=sys.stderr)
            return 1
        # 校验是 origin/main 祖先
        main_sha = run_git(["rev-parse", "--verify", "origin/main"])
        if main_sha.returncode != 0:
            print("WARN: 无 origin/main,跳过祖先校验(本地开发)", file=sys.stderr)
        else:
            anc = run_git(["merge-base", "--is-ancestor", sha, main_sha.stdout.strip()])
            if anc.returncode != 0:
                print(f"FATAL: {args.release_ref}({sha[:12]}) 不是 origin/main 祖先", file=sys.stderr)
                return 1
        print(f"resolved_target_sha={sha}")
    else:
        r = run_git(["rev-parse", "--verify", "origin/main"])
        if r.returncode != 0:
            # 本地无 origin → 用当前 HEAD(workflow 场景由 actions/checkout 提供 origin/main)
            r = run_git(["rev-parse", "HEAD"])
            print("WARN: 无 origin/main,用当前 HEAD(本地开发)", file=sys.stderr)
        print(f"resolved_target_sha={r.stdout.strip()}")
    print(f"scope={args.scope}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
