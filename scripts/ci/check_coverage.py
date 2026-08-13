"""覆盖率阈值检查:当前 ≥ 基线,且本分支阈值不降于目标分支。
用法:
  python scripts/ci/check_coverage.py --lang python --metric line --current 78.5
  python scripts/ci/check_coverage.py --lang python --metric line --current 78.5 --base-file <base分支的coverage.json>
当前 < 基线 → exit 1(防下调:base-file 为 base 分支版本时,本分支阈值 < base → 失败)。"""
import argparse
import json
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", required=True, choices=["python", "java", "web"])
    ap.add_argument("--metric", required=True, choices=["line", "branch"])
    ap.add_argument("--current", type=float, required=True)
    ap.add_argument("--base-file", default="evaluation/thresholds/coverage.json")
    args = ap.parse_args()

    with open(args.base_file, encoding="utf-8") as f:
        base = json.load(f)
    threshold = float(base[args.lang][args.metric])
    if args.current < threshold - 1e-9:
        print(f"FAIL: {args.lang}.{args.metric} {args.current} < 基线 {threshold}",
              file=sys.stderr)
        return 1
    print(f"OK: {args.lang}.{args.metric} {args.current} >= {threshold}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
