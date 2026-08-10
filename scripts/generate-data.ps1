# 生成 inventory 压测数据(幂等,会先清空 inventory)
# 用法: powershell -ExecutionPolicy Bypass -File scripts/generate-data.ps1 [-Rows 500000]
param([int]$Rows = 500000)
$env:INVENTORY_ROWS = "$Rows"
python "$PSScriptRoot/seed_data.py"
if ($LASTEXITCODE -ne 0) { throw "seed_data.py failed" }
Write-Host "done: $Rows rows"
