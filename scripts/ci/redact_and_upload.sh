#!/usr/bin/env bash
# 原始日志 → 脱敏 → Secret 扫描 → 上传 sanitized;失败标记 LOG_REDACTION_FAILED。
# 原始日志只在本地临时目录;Artifact 只引用 sanitized。
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RAW_DIR="${RUNNER_TEMP:-/tmp}/tracemind-raw-logs"
SAN_DIR="$REPO_ROOT/reports/generated/sanitized"

mkdir -p "$RAW_DIR"
# 收集原始日志(各阶段日志 + docker 容器日志)
if command -v docker >/dev/null 2>&1; then
  for c in $(docker ps --format '{{.Names}}' 2>/dev/null | grep '^tracemind-' || true); do
    docker logs "$c" > "$RAW_DIR/${c}.log" 2>&1 || true
  done
fi
find "$REPO_ROOT/reports/generated" -maxdepth 1 -name '*.log' -exec cp {} "$RAW_DIR/" \; 2>/dev/null || true

# 脱敏:Secret 从 env 传入,不进命令行参数
if ! python "$REPO_ROOT/scripts/ci/redact_logs.py" "$RAW_DIR" "$SAN_DIR"; then
  echo "::error::LOG_REDACTION_FAILED — 脱敏失败,不上传原始日志"
  rm -rf "$SAN_DIR"
  exit 1
fi
echo "sanitized logs → $SAN_DIR"
