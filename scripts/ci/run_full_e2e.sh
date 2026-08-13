#!/usr/bin/env bash
# Full E2E 编排:阶段执行 + 失败分类 + 部分报告 + 失败注入。
# 用法:
#   bash scripts/ci/run_full_e2e.sh --dry-run [--scope smoke|release]   # 只出计划,零副作用
#   bash scripts/ci/run_full_e2e.sh --scope smoke|release               # 真实执行
# 环境:
#   TRACEMIND_CI_FAIL_STAGE=<STAGE>   # 失败注入演练(该阶段直接失败,验证后续副作用被阻止)
#   COMPOSE_PROJECT_NAME               # 唯一 compose project(workflow 注入 run_id-run_attempt)
#   MYSQL_ROOT_PASSWORD / TRACEMIND_DB_*_PASSWORD / TRACEMIND_CHAT_*    # 由 Job Env 注入
set -uo pipefail

SCOPE="${TRACEMIND_CI_SCOPE:-smoke}"
FAIL_STAGE="${TRACEMIND_CI_FAIL_STAGE:-}"
DRY_RUN=0
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# Python 解释器:VM 无 python 命令(仅 python3);CI ubuntu 有 python
if command -v python3 >/dev/null 2>&1; then PYTHON="python3"; else PYTHON="python"; fi
REPORT_DIR="$REPO_ROOT/reports/generated"
REPORT_FILE="$REPORT_DIR/full-e2e.json"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-tracemind-ci-local}"
COMPOSE=(docker compose --project-name "$COMPOSE_PROJECT_NAME" -f "$REPO_ROOT/compose.yml" -f "$REPO_ROOT/compose.ci.yml")
# jaeger 在 compose.yml 有 observability-ui profile,CI 需激活
COMPOSE_UP=(docker compose --project-name "$COMPOSE_PROJECT_NAME" -f "$REPO_ROOT/compose.yml" -f "$REPO_ROOT/compose.ci.yml" --profile observability-ui)

STAGES=(BUILD DATA_INFRA_READY DB_MIGRATION BUSINESS_FIXTURE_SEED JAVA_INTEGRATION_TEST
        RAG_SEED APPLICATION_READY MCP_PROTOCOL_SMOKE OBSERVABILITY_WARMUP MODEL_SMOKE
        EVAL_AGENT_REAL SCN001_E2E SCN002_E2E REPLAY_BACKEND_VALIDATION)
PRIMARY=""
SECONDARY_JSON="[]"
CLEANUP="success"
FAILED_ANY=0

log() { echo "[$(date +%H:%M:%S)] $*"; }
die() { echo "FATAL: $*" >&2; exit 1; }

stage_impl() {
  # 各阶段真实实现;副作用阶段(DB/SCN 注入/处置)失败后由编排停止后续
  case "$1" in
    BUILD)
      "${COMPOSE[@]}" build mysql qdrant prometheus otel-collector jaeger order-service inventory-service ai-service
      ;;
    DATA_INFRA_READY)
      "${COMPOSE_UP[@]}" up -d mysql qdrant prometheus otel-collector jaeger
      "${COMPOSE_UP[@]}" ps --format '{{.Name}} {{.Status}}' | grep -E 'Up|healthy' >/dev/null || die "基础设施未就绪"
      ;;
    DB_MIGRATION)
      TRACEMIND_MIGRATE_DB_URL="mysql+pymysql://root:${MYSQL_ROOT_PASSWORD}@127.0.0.1:3306/" \
        "$PYTHON" "$REPO_ROOT/scripts/db/migrate.py" --init-db --migrations "$REPO_ROOT/scripts/db/migrations"
      TRACEMIND_MIGRATE_DB_URL="mysql+pymysql://root:${MYSQL_ROOT_PASSWORD}@127.0.0.1:3306/" \
        "$PYTHON" "$REPO_ROOT/scripts/db/migrate.py" --migrations "$REPO_ROOT/scripts/db/migrations"
      TRACEMIND_MIGRATE_DB_URL="mysql+pymysql://root:${MYSQL_ROOT_PASSWORD}@127.0.0.1:3306/" \
        "$PYTHON" "$REPO_ROOT/scripts/db/migrate.py" --provision --migrations "$REPO_ROOT/scripts/db/migrations"
      ;;
    BUSINESS_FIXTURE_SEED)
      "${COMPOSE[@]}" run --rm seed
      ;;
    JAVA_INTEGRATION_TEST)
      (cd "$REPO_ROOT/java" && mvn --batch-mode verify)
      ;;
    RAG_SEED)
      "$PYTHON" "$REPO_ROOT/scripts/seed_runbook.py"
      ;;
    APPLICATION_READY)
      "${COMPOSE_UP[@]}" up -d order-service inventory-service ai-service
      "${COMPOSE_UP[@]}" ps --format '{{.Name}} {{.Status}}' | grep -E 'healthy' >/dev/null || die "应用服务未 healthy"
      ;;
    MCP_PROTOCOL_SMOKE)
      "$PYTHON" "$REPO_ROOT/scripts/ci/check_mcp_protocol.py"
      ;;
    OBSERVABILITY_WARMUP)
      "$PYTHON" "$REPO_ROOT/scripts/ci/warmup_observability.py"
      ;;
    MODEL_SMOKE)
      (cd "$REPO_ROOT/ai-service" && uv run python ../scripts/smoke_llm.py)
      ;;
    EVAL_AGENT_REAL)
      (cd "$REPO_ROOT/ai-service" && uv run python ../scripts/eval_agent.py --mode offline --llm real_strict --runs 1)
      ;;
    SCN001_E2E)
      "$PYTHON" "$REPO_ROOT/scripts/verify-m14.py" --base http://localhost:8000 --order http://localhost:8081 \
        --scenario SCN-001 --rounds "$SCN_ROUNDS" --interval 0
      ;;
    SCN002_E2E)
      "$PYTHON" "$REPO_ROOT/scripts/verify-m14.py" --base http://localhost:8000 --order http://localhost:8081 \
        --scenario SCN-002 --rounds "$SCN_ROUNDS" --interval 0
      ;;
    REPLAY_BACKEND_VALIDATION)
      "$PYTHON" "$REPO_ROOT/scripts/verify-m15.py" --base http://localhost:8000 --order http://localhost:8081
      ;;
  esac
}

run_stage() {
  local stage="$1"
  local t0 t1 dur
  t0=$(date +%s%3N)
  log "=== $stage ==="
  mkdir -p "$REPORT_DIR"
  if [ -n "$FAIL_STAGE" ] && [ "$FAIL_STAGE" = "$stage" ]; then
    log "  FAIL-INJECT: $stage(TRACEMIND_CI_FAIL_STAGE=$FAIL_STAGE)"
    echo "{\"stage\":\"$stage\",\"status\":\"failed\",\"failureCategory\":\"${stage}_FAILED\",\"injected\":true}" >> "$REPORT_FILE"
    PRIMARY="${stage}_FAILED"
    FAILED_ANY=1
    return 1
  fi
  if ! stage_impl "$stage" > "$REPORT_DIR/${stage}.log" 2>&1; then
    local code=$?
    log "  FAIL: $stage(exit=$code) → 日志 reports/generated/${stage}.log"
    echo "{\"stage\":\"$stage\",\"status\":\"failed\",\"failureCategory\":\"${stage}_FAILED\",\"exitCode\":$code}" >> "$REPORT_FILE"
    PRIMARY="${stage}_FAILED"
    FAILED_ANY=1
    return 1
  fi
  t1=$(date +%s%3N); dur=$((t1 - t0))
  echo "{\"stage\":\"$stage\",\"status\":\"success\",\"durationMs\":$dur}" >> "$REPORT_FILE"
  log "  OK: $stage(${dur}ms)"
}

finalize_report() {
  "$PYTHON" - "$REPORT_FILE" "$PRIMARY" "$SECONDARY_JSON" "$CLEANUP" "$SCOPE" <<'EOF'
import json, sys
path, primary, secondary, cleanup, scope = sys.argv[1:]
data = {"scope": scope}
try:
    with open(path, encoding="utf-8") as f:
        stages = [json.loads(line) for line in f if line.strip().startswith("{")]
    data["stages"] = stages
except FileNotFoundError:
    data["stages"] = []
data["primaryFailureCategory"] = primary or None
data["secondaryFailures"] = json.loads(secondary)
data["cleanupStatus"] = cleanup
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f"REPORT: {path}")
EOF
}

cleanup() {
  log "=== COMPOSE_CLEANUP ==="
  if "${COMPOSE[@]}" down -v --remove-orphans > /dev/null 2>&1; then
    CLEANUP="success"
  else
    CLEANUP="failed"
    SECONDARY_JSON='["CLEANUP_FAILED"]'
  fi
}

main() {
  mkdir -p "$REPORT_DIR"
  : > "$REPORT_FILE"
  if [ "$SCOPE" = "smoke" ]; then SCN_ROUNDS=1; else SCN_ROUNDS=3; fi
  log "Full E2E scope=$SCOPE rounds=$SCN_ROUNDS fail_inject=${FAIL_STAGE:-无} project=$COMPOSE_PROJECT_NAME"
  for stage in "${STAGES[@]}"; do
    if [ "$DRY_RUN" = 1 ]; then
      log "[dry-run] PLAN $stage"
      continue
    fi
    if ! run_stage "$stage"; then
      log "  → 停止后续有副作用阶段(primary=${PRIMARY})"
      break
    fi
  done
  finalize_report
  [ "$DRY_RUN" = 1 ] && { log "dry-run 完成(零副作用)"; exit 0; }
  cleanup
  log "Full E2E 结束: primary=${PRIMARY:-无} cleanup=$CLEANUP"
  [ "$FAILED_ANY" = 0 ] || exit 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --scope) SCOPE="$2"; shift 2 ;;
    *) echo "未知参数 $1" >&2; exit 2 ;;
  esac
done

main
