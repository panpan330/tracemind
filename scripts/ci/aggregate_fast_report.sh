#!/usr/bin/env bash
# 汇总各 Job Artifact 为总报告(输入:Artifact 目录;输出:fast-summary/)。
set -euo pipefail

SRC="${1:?artifact 目录}"
OUT="${2:-fast-summary}"
mkdir -p "$OUT"
{
  echo "# Fast Gate 汇总报告"
  echo
  echo "- 生成时间: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "- Artifact 来源: $SRC"
  echo
  echo "## Artifact 文件清单"
  find "$SRC" -type f | sort
} > "$OUT/summary.md"
echo "aggregate OK → $OUT/summary.md"
