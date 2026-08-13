# TraceMind database init script (idempotent, safe to re-run)
# 统一迁移入口:调用 scripts/db/migrate.py(建库 + Schema 迁移 + 账号 Provisioning)。
# Usage: set $env:MYSQL_ROOT_PASSWORD first, then:
#   powershell -ExecutionPolicy Bypass -File scripts/init-database.ps1
# 账号密码从以下环境变量读(避免写入 SQL 文件/脚本):
#   TRACEMIND_DB_CONTROL_APP_PASSWORD / TRACEMIND_DB_APP_BUSINESS_PASSWORD /
#   TRACEMIND_DB_AI_INVESTIGATOR_PASSWORD / TRACEMIND_DB_FIX_EXECUTOR_PASSWORD /
#   TRACEMIND_DB_SESSION_TERMINATOR_PASSWORD
param(
    [string]$RootPassword = $env:MYSQL_ROOT_PASSWORD,
    [string]$DbHost = "localhost",
    [int]$DbPort = 3306
)

if (-not $RootPassword) {
    throw "Set MYSQL_ROOT_PASSWORD env var first, e.g. `$env:MYSQL_ROOT_PASSWORD = 'your-root-password'"
}

$python = "python"
$migratePy = Join-Path $PSScriptRoot "db\migrate.py"
$migrationsDir = Join-Path $PSScriptRoot "db\migrations"

Write-Host "==> create databases (idempotent)"
$env:TRACEMIND_MIGRATE_DB_URL = "mysql+pymysql://root:${RootPassword}@${DbHost}:${DbPort}/"
& $python $migratePy --migrations $migrationsDir --init-db
if ($LASTEXITCODE -ne 0) { throw "failed to create databases" }

Write-Host "==> schema migrations (idempotent, checksum-guarded)"
& $python $migratePy --migrations $migrationsDir
if ($LASTEXITCODE -ne 0) { throw "failed to run migrations" }

Write-Host "==> account provisioning (passwords from env vars)"
& $python $migratePy --provision --migrations $migrationsDir
if ($LASTEXITCODE -ne 0) { throw "failed to provision accounts" }

Write-Host "==> init done"
Write-Host "    dbs: tracemind_business / tracemind_business_test / tracemind_control"
Write-Host "    users: app_business / tracemind_control_app / ai_investigator / fix_executor / session_terminator"
Write-Host "    migrations: scripts/db/migrations/*.sql(全部版本化,含 v12/v13)"
