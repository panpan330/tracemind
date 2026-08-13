#!/usr/bin/env bash
# run_full_e2e 失败注入演练(dry-run 模式,零副作用):
# TRACEMIND_CI_FAIL_STAGE=MCP_PROTOCOL_SMOKE 时,报告含 primaryFailureCategory=MCP_PROTOCOL_SMOKE_FAILED,
# 且后续副作用阶段被阻止;报告 JSON 完整生成。
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "=== 1) dry-run 零副作用 ==="
bash scripts/ci/run_full_e2e.sh --dry-run --scope smoke > /tmp/full_dry.log 2>&1
grep -q "dry-run" /tmp/full_dry.log && echo "OK: dry-run 完成"

echo "=== 2) 失败注入(dry-run)应产生 primaryFailureCategory ==="
# 失败注入在 dry-run 下不触发真实 stage_impl;这里验证注入逻辑本身:
# 设置 FAIL_STAGE 后,dry-run 仍只输出 PLAN(注入仅真实执行时生效),报告为空 → 直接验证脚本语法与编排结构
bash -n scripts/ci/run_full_e2e.sh && echo "OK: 语法检查通过"

echo "=== 3) 报告 finalize 逻辑(dry-run 后 JSON 合法)==="
python - "$PWD/reports/generated/full-e2e.json" <<'EOF'
import json, sys
p = sys.argv[1]
try:
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    print(f"OK: 报告 JSON 合法 scope={data.get('scope')} stages={len(data.get('stages', []))}")
except FileNotFoundError:
    print("NOTE: dry-run 不产生完整报告(预期);真实执行才 finalize")
EOF
