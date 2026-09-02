# TraceMind 主机侧干净克隆启动门禁(V2.0-A)
# 从安全 example 生成本地忽略文件(.env.vm / secrets/mcp_clients.json / ai-service/.env.local),
# 运行官方数据库迁移器,校验依赖。绝不提交真实密钥。
# 用法: powershell -ExecutionPolicy Bypass -File scripts/bootstrap-dev.ps1
param(
    [string]$MySqlHost = "localhost",
    [int]$MySqlPort = 3306,
    [string]$RootPassword = $env:MYSQL_ROOT_PASSWORD
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Write-Host "==> [1/4] 从安全 example 生成本地忽略文件"
if (-not (Test-Path "$root\.env.vm")) {
    Copy-Item "$root\.env.vm.example" "$root\.env.vm"
    Write-Host "    已生成 .env.vm(占位;fake 模式无需真实 key,真实模式自行填写)"
} else { Write-Host "    .env.vm 已存在,跳过" }

New-Item -ItemType Directory -Force -Path "$root\secrets" | Out-Null
if (-not (Test-Path "$root\secrets\mcp_clients.json")) {
    $token = -join ((1..32) | ForEach-Object { "{0:x}" -f (Get-Random -Max 16) })
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $fp = "sha256:" + (($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($token)) |
        ForEach-Object { $_.ToString("x2") }) -join "")
    $json = @{
        "__origins__" = @()
        $fp = @{ subject = "ai-service"; audience = "tracemind-mcp-tools";
                 scopes = @("tools:investigate") }
    } | ConvertTo-Json -Depth 3
    Set-Content -Path "$root\secrets\mcp_clients.json" -Value $json -Encoding UTF8
    Write-Host "    已生成 secrets/mcp_clients.json"
    Write-Host "    >>> ai-service 侧请设置 TRACEMIND_MCP_HTTP_BEARER_TOKEN=$token(勿提交)"
} else { Write-Host "    secrets/mcp_clients.json 已存在,跳过" }

if (-not (Test-Path "$root\ai-service\.env.local")) {
    Copy-Item "$root\.env.example" "$root\ai-service\.env.local"
    Write-Host "    已生成 ai-service/.env.local(默认 fake 模式 + localhost DB)"
} else { Write-Host "    ai-service/.env.local 已存在,跳过" }

Write-Host "==> [2/4] 依赖校验"
foreach ($cmd in @("python", "docker")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) { Write-Host "    $cmd OK" }
    else { Write-Host "    [警告] $cmd 不可用(主机侧启动 ai-service 需 uv;compose 校验需 docker)" }
}

if ($RootPassword) {
    Write-Host "==> [3/4] 初始化/迁移本机 MySQL(官方迁移器,幂等)"
    $env:TRACEMIND_MIGRATE_DB_URL = "mysql+pymysql://root:${RootPassword}@${MySqlHost}:${MySqlPort}/"
    python "$root\scripts\db\migrate.py" --init-db --migrations "$root\scripts\db\migrations"
    if ($LASTEXITCODE -ne 0) { throw "建库失败" }
    python "$root\scripts\db\migrate.py" --migrations "$root\scripts\db\migrations"
    if ($LASTEXITCODE -ne 0) { throw "迁移失败" }
    python "$root\scripts\db\migrate.py" --provision --migrations "$root\scripts\db\migrations"
    if ($LASTEXITCODE -ne 0) { throw "账号 Provisioning 失败" }
} else {
    Write-Host "==> [3/4] 跳过 MySQL 迁移(未设 MYSQL_ROOT_PASSWORD;可先 `$env:MYSQL_ROOT_PASSWORD='...' 再重跑)"
}

Write-Host "==> [4/4] 启动顺序提示(docs/startup-boundary.md)"
Write-Host "    本机: MySQL(常驻) → Java 两服务(mvn spring-boot:run) → ai-service(uvicorn) → web(npm run dev)"
Write-Host "    VM Docker 基础设施: docker compose up -d qdrant;观测栈 --profile observability-ui(可选)"
Write-Host "    混合联调: fake/fixture 模式不依赖观测栈;真实观测需在 VM 拉起 prometheus/jaeger(见 scripts/vm-infra-check.sh)"
Write-Host "bootstrap 完成"
