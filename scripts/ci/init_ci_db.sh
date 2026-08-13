#!/usr/bin/env bash
# CI 数据库初始化编排:等 MySQL → 建库 → 迁移 → 账号 → fixture → 五账号探针 + 字符集/时区断言。
# 用法: bash scripts/ci/init_ci_db.sh
# 依赖:mysql CLI、python3(scripts/db/migrate.py);密码从环境变量读(MYSQL_ROOT_PASSWORD + TRACEMIND_DB_*_PASSWORD)。
set -euo pipefail

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-3306}"
MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:?需设置 MYSQL_ROOT_PASSWORD}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# Python 解释器:可注入(CI 用 ai-service venv);缺省优先 python3(VM)
if [ -n "${CI_PYTHON:-}" ]; then
  PYTHON="$CI_PYTHON"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
else
  PYTHON="python"
fi

mysql_() {
  mysql --host="$DB_HOST" --port="$DB_PORT" --user="$1" "--password=$2" "${@:3}"
}

echo "[init_ci_db] 1) 等待 MySQL ${DB_HOST}:${DB_PORT} ..."
for i in $(seq 1 60); do
  if mysqladmin ping -h"$DB_HOST" -P"$DB_PORT" -uroot "--password=$MYSQL_ROOT_PASSWORD" --silent 2>/dev/null; then
    break
  fi
  [ "$i" = 60 ] && { echo "FATAL: MySQL 未就绪" >&2; exit 1; }
  sleep 1
done

echo "[init_ci_db] 2) 建库 + 迁移 + 账号 Provisioning"
export TRACEMIND_MIGRATE_DB_URL="mysql+pymysql://root:${MYSQL_ROOT_PASSWORD}@${DB_HOST}:${DB_PORT}/"
"$PYTHON" "$REPO_ROOT/scripts/db/migrate.py" --init-db --migrations "$REPO_ROOT/scripts/db/migrations"
"$PYTHON" "$REPO_ROOT/scripts/db/migrate.py" --migrations "$REPO_ROOT/scripts/db/migrations"
"$PYTHON" "$REPO_ROOT/scripts/db/migrate.py" --provision --migrations "$REPO_ROOT/scripts/db/migrations"

echo "[init_ci_db] 3) 最小确定性 fixture(可选:由调用方提供 SQL)"
if [ -n "${CI_FIXTURE_SQL:-}" ] && [ -f "$CI_FIXTURE_SQL" ]; then
  mysql_ root "$MYSQL_ROOT_PASSWORD" < "$CI_FIXTURE_SQL"
fi

echo "[init_ci_db] 4) 五账号权限探针"
probe_account() {
  local user="$1" pwd="$2" ok_sql="$3"
  if mysql_ "$user" "$pwd" -e "$ok_sql" >/dev/null 2>&1; then
    echo "  OK   $user:$ok_sql"
  else
    echo "  FAIL $user 正向探针失败: $ok_sql" >&2
    exit 1
  fi
}
probe_reject() {
  local user="$1" pwd="$2" bad_sql="$3" expect_fail="$4"
  if mysql_ "$user" "$pwd" -e "$bad_sql" >/dev/null 2>&1; then
    if [ "$expect_fail" = "1" ]; then
      echo "  FAIL $user 越权操作成功(应失败): $bad_sql" >&2
      exit 1
    fi
    echo "  OK   $user 允许:$bad_sql"
  else
    if [ "$expect_fail" = "1" ]; then
      echo "  OK   $user 正确拒绝:$bad_sql"
    else
      echo "  FAIL $user 允许操作失败(应成功): $bad_sql" >&2
      exit 1
    fi
  fi
}

CONTROL_PWD="${TRACEMIND_DB_CONTROL_APP_PASSWORD:?缺 TRACEMIND_DB_CONTROL_APP_PASSWORD}"
BUSINESS_PWD="${TRACEMIND_DB_APP_BUSINESS_PASSWORD:?缺 TRACEMIND_DB_APP_BUSINESS_PASSWORD}"
INVESTIGATOR_PWD="${TRACEMIND_DB_AI_INVESTIGATOR_PASSWORD:?缺 TRACEMIND_DB_AI_INVESTIGATOR_PASSWORD}"
FIX_PWD="${TRACEMIND_DB_FIX_EXECUTOR_PASSWORD:?缺 TRACEMIND_DB_FIX_EXECUTOR_PASSWORD}"
TERMINATOR_PWD="${TRACEMIND_DB_SESSION_TERMINATOR_PASSWORD:?缺 TRACEMIND_DB_SESSION_TERMINATOR_PASSWORD}"

# 1) tracemind_control_app:控制库读写
probe_account tracemind_control_app "$CONTROL_PWD" \
  "SELECT 1 FROM tracemind_control.schema_migrations LIMIT 1"
# 2) app_business:业务库读写(仅业务范围)
probe_account app_business "$BUSINESS_PWD" \
  "SELECT COUNT(*) FROM tracemind_business.inventory"
probe_reject app_business "$BUSINESS_PWD" \
  "DROP TABLE tracemind_control.schema_migrations" 1
# 3) ai_investigator:只读 + processlist 观测;INSERT/KILL 必须失败
probe_account ai_investigator "$INVESTIGATOR_PWD" \
  "SELECT COUNT(*) FROM tracemind_business.inventory"
probe_reject ai_investigator "$INVESTIGATOR_PWD" \
  "INSERT INTO tracemind_business.inventory (sku_id, warehouse_id, quantity) VALUES (999,1,1)" 1
probe_reject ai_investigator "$INVESTIGATOR_PWD" "KILL 1" 1
# 4) fix_executor:仅 INDEX(无 DML)
probe_reject fix_executor "$FIX_PWD" \
  "INSERT INTO tracemind_control.incident (title) VALUES ('x')" 1
# 5) session_terminator:能查 processlist(有 PROCESS),不能 DDL
probe_account session_terminator "$TERMINATOR_PWD" \
  "SELECT COUNT(*) FROM performance_schema.processlist"
probe_reject session_terminator "$TERMINATOR_PWD" "CREATE DATABASE hack" 1

echo "[init_ci_db] 5) 字符集/时区断言"
cs="$(mysql_ root "$MYSQL_ROOT_PASSWORD" -N -e 'SELECT @@character_set_server')"
coll="$(mysql_ root "$MYSQL_ROOT_PASSWORD" -N -e 'SELECT @@collation_server')"
tz="$(mysql_ root "$MYSQL_ROOT_PASSWORD" -N -e 'SELECT @@global.time_zone')"
echo "  charset_server=$cs collation_server=$coll time_zone=$tz"
case "$cs" in utf8mb4*) ;; *) echo "FAIL: 字符集 $cs 非 utf8mb4" >&2; exit 1;; esac
[ -n "$coll" ] || { echo "FAIL: collation 为空" >&2; exit 1; }
[ -n "$tz" ] || { echo "FAIL: time_zone 为空" >&2; exit 1; }

echo "[init_ci_db] OK"
