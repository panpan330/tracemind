# M1 acceptance: compare EXPLAIN and P95 between HEALTHY and FAULTY states
# Prereq: both services running; inventory service started with DEMO_MODE=true
# Usage: powershell -ExecutionPolicy Bypass -File scripts/verify-m1.ps1 [-DemoKey "demo-secret-2026"]
param([string]$DemoKey = $env:DEMO_KEY)

if (-not $DemoKey) { throw "Set DEMO_KEY or pass -DemoKey" }

$inv = "http://localhost:8082"

Write-Host "==> 1) reset to HEALTHY"
curl.exe -s -X POST "$inv/internal/scenarios/SCN-001/reset" -H "x-demo-key: $DemoKey" | Out-Null

Write-Host "==> 2) healthy load 20s"
$env:LOAD_DURATION_SECONDS = "20"; $env:LOAD_QPS = "20"
& python "$PSScriptRoot/loadgen.py"
$healthy = curl.exe -s "$inv/internal/observations/metrics?window_seconds=300" | ConvertFrom-Json
Write-Host "HEALTHY p95=$($healthy.p95_ms)"

Write-Host "==> 3) EXPLAIN (healthy, uses idx_sku_warehouse)"
& mysql -uapp_business -papp_business_pwd tracemind_business -e "EXPLAIN SELECT id FROM inventory WHERE sku_id=42 AND warehouse_id=7;" 2>$null

Write-Host "==> 4) inject fault, faulty load 20s"
curl.exe -s -X POST "$inv/internal/scenarios/SCN-001/inject" -H "x-demo-key: $DemoKey" | Out-Null
& python "$PSScriptRoot/loadgen.py"
$faulty = curl.exe -s "$inv/internal/observations/metrics?window_seconds=300" | ConvertFrom-Json
Write-Host "FAULTY  p95=$($faulty.p95_ms)"

Write-Host "==> 5) EXPLAIN (faulty, full scan)"
& mysql -uapp_business -papp_business_pwd tracemind_business -e "EXPLAIN SELECT id FROM inventory WHERE sku_id=42 AND warehouse_id=7;" 2>$null

Write-Host "==> 6) restore HEALTHY"
curl.exe -s -X POST "$inv/internal/scenarios/SCN-001/reset" -H "x-demo-key: $DemoKey" | Out-Null

Write-Host ""
Write-Host "========== RESULT =========="
Write-Host "HEALTHY p95=$($healthy.p95_ms)  FAULTY p95=$($faulty.p95_ms)"
Write-Host "(expect: FAULTY EXPLAIN type=ALL / large rows; HEALTHY uses idx_sku_warehouse; FAULTY p95 higher)"
