# 负载发生器封装: powershell -ExecutionPolicy Bypass -File scripts/run-load.ps1 [-Seconds 60] [-Qps 20]
param([int]$Seconds = 60, [int]$Qps = 20)
$env:LOAD_DURATION_SECONDS = "$Seconds"
$env:LOAD_QPS = "$Qps"
python "$PSScriptRoot/loadgen.py"
if ($LASTEXITCODE -ne 0) { throw "loadgen failed" }
