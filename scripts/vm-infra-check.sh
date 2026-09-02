#!/usr/bin/env bash
# TraceMind VM Docker 基础设施门禁(V2.0-A)— 在 VM 上执行(或经 ssh 调用)。
# 只校验原本由 Docker 承载的基础设施(qdrant/观测栈),不在 VM 部署主机侧应用。
# 用法: bash scripts/vm-infra-check.sh [compose 项目目录,默认 ~/tracemind]
set -uo pipefail
DIR="${1:-$HOME/tracemind}"

fail=0
echo "==> [1/3] Docker 可用性"
docker version --format 'docker {{.Server.Version}}' || fail=1

echo "==> [2/3] Compose 配置校验(基础设施;需 bootstrap 已生成 .env.vm / secrets/mcp_clients.json)"
if [ -f "$DIR/compose.yml" ]; then
  (cd "$DIR" && docker compose config --quiet) || fail=1
  echo "    compose config OK: $DIR"
else
  echo "    [跳过] $DIR/compose.yml 不存在(传参指定仓库目录)"
fi

echo "==> [3/3] 基础设施容器健康与端口"
# qdrant:长期记忆存储(混合拓扑中固定在 VM Docker)
qdrant_cid="$(docker ps -q --filter name=qdrant | head -1)"
if [ -n "$qdrant_cid" ]; then
  docker ps --filter name=qdrant --format '    qdrant: {{.Names}} {{.Status}} {{.Ports}}'
  port="$(docker port "$(docker ps -q --filter name=qdrant | head -1)" 6333/tcp 2>/dev/null | head -1 | cut -d: -f3)"
  [ -n "${port:-}" ] && curl -sf "http://127.0.0.1:${port}/healthz" >/dev/null \
    && echo "    qdrant /healthz OK (:${port})" || { echo "    qdrant /healthz FAIL"; fail=1; }
else
  echo "    [提示] qdrant 未运行: docker compose up -d qdrant"; fail=1
fi
# 观测栈(profile observability-ui):仅真实观测验收需要,fake/fixture 模式不依赖
for svc in prometheus jaeger otel-collector grafana; do
  cid="$(docker ps -q --filter name="tracemind-$svc")"
  if [ -n "$cid" ]; then
    echo "    $svc: 运行中"
  else
    echo "    $svc: 未运行(fake/fixture 模式无需;真实观测验收前: docker compose --profile observability-ui up -d)"
  fi
done

exit $fail
