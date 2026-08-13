#!/usr/bin/env bash
# Fast 汇聚校验:读 5 个 env(PYTHON/JAVA/WEB/EVALUATION/CI_QUALITY_RESULT),
# 任一非 success(含 failure/cancelled/skipped)→ exit 1。
set -uo pipefail

ok=1
for v in PYTHON_RESULT JAVA_RESULT WEB_RESULT EVALUATION_RESULT CI_QUALITY_RESULT; do
  val="${!v:-missing}"
  echo "  $v=$val"
  [ "$val" = "success" ] || ok=0
done
if [ "$ok" = 1 ]; then
  echo "OK: 全部上游 Job success"
  exit 0
fi
echo "FAIL: 存在非 success 上游 Job" >&2
exit 1
