"""校验目标 SHA 的 fast-gate Check Run 已成功(绑定 workflow 文件 + app.slug)。
用法: python scripts/ci/verify_fast_gate.py --sha <sha> [--repo owner/repo] [--token <gh>]
环境:GITHUB_TOKEN(workflow 注入)或 --token。
"""
import argparse
import json
import os
import sys
import urllib.request

FAST_GATE_WORKFLOW = ".github/workflows/fast-gate.yml"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sha", required=True)
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    args = ap.parse_args()

    if not args.repo:
        print("FATAL: 需 --repo 或 GITHUB_REPOSITORY", file=sys.stderr)
        return 1
    if not args.token:
        print("FATAL: 需 --token 或 GITHUB_TOKEN", file=sys.stderr)
        return 1

    url = f"https://api.github.com/repos/{args.repo}/commits/{args.sha}/check-runs"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {args.token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "tracemind-ci",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: GitHub API 调用失败: {e}", file=sys.stderr)
        return 1

    # 严格匹配:name=fast-gate + app.slug=github-actions + completed/success
    for cr in data.get("check_runs", []):
        app = cr.get("app") or {}
        if (cr.get("name") == "fast-gate"
                and cr.get("status") == "completed"
                and cr.get("conclusion") == "success"
                and app.get("slug") == "github-actions"):
            print(f"OK: fast-gate success @ {args.sha[:12]}")
            return 0

    print(f"FAIL: 未找到 {args.sha[:12]} 的成功 fast-gate(github-actions)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
