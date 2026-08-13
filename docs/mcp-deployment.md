# MCP Streamable HTTP 部署与手工配置(V1.7)

## 部署顺序(标准部署)

1. **数据库迁移**(追加式,向后兼容):`python scripts/db/migrate.py --init-db --migrations scripts/db/migrations`(新增 `tool_call_attempt` 表 / `mcp_tool_auditor` 账号)。
2. **部署 mcp-tools**(独立容器):`docker compose build mcp-tools && docker compose up -d mcp-tools`。
3. **MCP 协议与契约探针**:`curl -sf http://<mcp-tools-host>:8001/health/ready` 返回 200。
4. **部署 ai-service**:`docker compose up -d ai-service`(启动时校验 Server Contract,不兼容 Fail-closed)。
5. **VM Smoke**:`python scripts/verify-m17.py --tier vm-smoke`。

## 手工配置项

| 项 | 位置 | 说明 |
|---|---|---|
| `TRACEMIND_MCP_HTTP_BEARER_TOKEN` | ai-service env | 客户端 Bearer Token(部署时生成,不进 git) |
| `secrets/mcp_clients.json` | compose secrets(挂载到 mcp-tools) | `{ "sha256:<token-hash>": {"subject":"ai-service","audience":"tracemind-mcp-tools","scopes":["tools:investigate"]} }` |
| `TRACEMIND_DB_MCP_AUDITOR_PASSWORD` | compose env / .env.vm | `mcp_tool_auditor` 账号密码(Provisioning 注入,不在迁移 SQL) |
| `TRACEMIND_MCP_AUDIT_DB_URL` | mcp-tools env | `mcp_tool_auditor` 的最小权限审计连接 |

## 关键安全不变量

- 认证:Opaque Token,Fingerprint 映射 Principal;`client_id` 只能从认证结果派生。
- Origin:存在必须命中 Allowlist;缺失 → 认证后放行(服务间调用);禁 `*`。
- 上下文注入:`X-TraceMind-Incident-Id / -Agent-Run-Id / -Tool-Call-Id / -Purpose / -Context-Version` 逐请求生成;模型传同名伪造字段 → 拒绝。
- 审计唯一所有者:AI 写 `tool_call`(先提交事务再发请求);MCP Server 写 `tool_call_attempt`(两段式 fail-closed)。
- 网络:三内部网络(`agent-mcp-network`/`control-data-network`/`tool-observation-network`,均 `internal: true`)+ `llm-egress-network`(仅 ai-service);mcp-tools 不映射宿主机端口。
- 凭据:ai-service 不持有调查凭据;mcp-tools 不持有 LLM key / fix_executor / session_terminator / 业务写账号。

## 回滚原则

数据库 Migration 追加式(不删旧字段);AI Client 启动校验 Server Contract,不兼容 Fail-closed;未定义 N-1 兼容矩阵前,不声称可任意单独回滚 MCP Server。
