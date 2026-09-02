#!/usr/bin/env bash
# TraceMind 主机侧干净克隆启动门禁(V2.0-A)— bootstrap-dev.ps1 的等价 shell 版。
# 从安全 example 生成本地忽略文件(.env.vm / secrets/mcp_clients.json / ai-service/.env.local),
# 运行官方数据库迁移器,校验依赖。绝不提交真实密钥。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> [1/4] 从安全 example 生成本地忽略文件"
[ -f "$ROOT/.env.vm" ] && echo "    .env.vm 已存在,跳过" || {
  cp "$ROOT/.env.vm.example" "$ROOT/.env.vm"
  echo "    已生成 .env.vm(占位;fake 模式无需真实 key)"
}
mkdir -p "$ROOT/secrets"
if [ ! -f "$ROOT/secrets/mcp_clients.json" ]; then
  TOKEN="$(python -c 'import secrets; print(secrets.token_hex(32))')"
  FP="$(python -c "import hashlib,sys; print('sha256:'+hashlib.sha256(sys.argv[1].encode()).hexdigest())" "$TOKEN")"
  python - "$FP" > "$ROOT/secrets/mcp_clients.json" <<'PY'
import json, sys
print(json.dumps({
    "__origins__": [],
    sys.argv[1]: {"subject": "ai-service", "audience": "tracemind-mcp-tools",
                  "scopes": ["tools:investigate"]},
}, indent=2))
PY
  echo "    已生成 secrets/mcp_clients.json"
  echo "    >>> ai-service 侧请设置 TRACEMIND_MCP_HTTP_BEARER_TOKEN=$TOKEN(勿提交)"
else
  echo "    secrets/mcp_clients.json 已存在,跳过"
fi
[ -f "$ROOT/ai-service/.env.local" ] && echo "    ai-service/.env.local 已存在,跳过" || {
  cp "$ROOT/.env.example" "$ROOT/ai-service/.env.local"
  echo "    已生成 ai-service/.env.local(默认 fake 模式 + localhost DB)"
}

echo "==> [2/4] 依赖校验"
for cmd in python docker; do
  command -v "$cmd" >/dev/null 2>&1 && echo "    $cmd OK" \
    || echo "    [警告] $cmd 不可用"
done

if [ -n "${MYSQL_ROOT_PASSWORD:-}" ]; then
  echo "==> [3/4] 初始化/迁移本机 MySQL(官方迁移器,幂等)"
  export TRACEMIND_MIGRATE_DB_URL="mysql+pymysql://root:${MYSQL_ROOT_PASSWORD}@localhost:3306/"
  python "$ROOT/scripts/db/migrate.py" --init-db --migrations "$ROOT/scripts/db/migrations"
  python "$ROOT/scripts/db/migrate.py" --migrations "$ROOT/scripts/db/migrations"
  python "$ROOT/scripts/db/migrate.py" --provision --migrations "$ROOT/scripts/db/migrations"
else
  echo "==> [3/4] 跳过 MySQL 迁移(未设 MYSQL_ROOT_PASSWORD)"
fi

echo "==> [4/4] 启动顺序提示(docs/startup-boundary.md)"
echo "    本机: MySQL(常驻) → Java 两服务(mvn spring-boot:run) → ai-service(uvicorn) → web(npm run dev)"
echo "    VM Docker 基础设施: docker compose up -d qdrant;观测栈 --profile observability-ui(可选)"
echo "    混合联调: fake/fixture 模式不依赖观测栈;真实观测需在 VM 拉起 prometheus/jaeger(scripts/vm-infra-check.sh)"
echo "bootstrap 完成"
