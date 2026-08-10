# TraceMind database init script (idempotent, safe to re-run)
# Usage: set $env:MYSQL_ROOT_PASSWORD first, then:
#   powershell -ExecutionPolicy Bypass -File scripts/init-database.ps1
param(
    [string]$RootPassword = $env:MYSQL_ROOT_PASSWORD,
    [string]$DbHost = "localhost",
    [int]$DbPort = 3306
)

if (-not $RootPassword) {
    throw "Set MYSQL_ROOT_PASSWORD env var first, e.g. `$env:MYSQL_ROOT_PASSWORD = 'your-root-password'"
}

$sqlDir = Join-Path $PSScriptRoot "sql"
$mysqlArgs = @("-h", $DbHost, "-P", "$DbPort", "-uroot", "-p$RootPassword", "-e")

Write-Host "==> create databases (idempotent)"
& mysql @mysqlArgs "source $(Join-Path $sqlDir '01-create-db.sql')"
if ($LASTEXITCODE -ne 0) { throw "failed to create databases" }

Write-Host "==> create users and grants (idempotent)"
& mysql @mysqlArgs "source $(Join-Path $sqlDir '02-users.sql')"
if ($LASTEXITCODE -ne 0) { throw "failed to create users" }

Write-Host "==> business DDL (idempotent)"
& mysql @mysqlArgs "source $(Join-Path $sqlDir '03-schema.sql')"
if ($LASTEXITCODE -ne 0) { throw "failed to create tables" }

Write-Host "==> control schema DDL (idempotent)"
& mysql @mysqlArgs "source $(Join-Path $sqlDir '04-control-schema.sql')"
if ($LASTEXITCODE -ne 0) { throw "failed to create control tables" }

Write-Host "==> init done"
Write-Host "    dbs: tracemind_business / tracemind_business_test / tracemind_control"
Write-Host "    users: app_business / tracemind_control_app / ai_investigator / fix_executor"
Write-Host "    tables: inventory(idx_sku_warehouse) / orders / order_item / scenario_audit"
