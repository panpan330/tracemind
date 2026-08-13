# V1.7 设计:MCP Streamable HTTP 远程传输与服务化

日期:2026-08-13
状态:已定稿(四段设计 + 三轮外部审查把控,51 条意见逐条收敛)
前置:V1.2(MCP stdio 工具层协议化)→ V1.3(多场景)→ V1.4(真实观测)→ V1.5(回放)→ V1.6(迁移器/Profile/真实模型验收)

## 1. 背景与目标

V1.2 起 MCP Server 由 AI 服务 Spawn 子进程、经 stdio 调用,受"必须与 AI 服务同机"限制。V1.7 将其升级为**独立远程工具服务**:

- 独立进程、独立容器、独立凭据、独立扩缩容、独立回滚;
- MCP 标准传输从 stdio 扩展为 **Streamable HTTP**(旧 HTTP+SSE 双端点已弃用,不采用);
- 供多个合规 MCP Client 复用(架构能力证明,不作 V1.7 开放接入)。

**运行链路**:

```
LangGraph Agent → MCP Client → Streamable HTTP → tracemind-mcp-tools(独立容器)
                                                       │
                                    ToolExecutionService(7 个只读调查工具)
                                                       │
                                    MySQL / Prometheus / Jaeger / Java 服务
```

**仍然保持**:`execute_fix` / `verify_recovery` 不暴露为远程 MCP Tool,继续作为 AI 控制服务内部的确定性安全节点;恢复判定规则留 AI 服务,事实采集(指标/Trace/索引/锁状态)仍经 MCP 获取,不因 verify_recovery 是内部节点而新增 direct 调查路径。

## 2. 传输定位

两种传输 = 两个 Adapter,同一套工具实现(同一 ToolRegistry / 同一参数校验 / 同一审计逻辑 / 同一 Fixture 语义)。

| 环境 | 传输 |
|---|---|
| 单元/集成测试 | Stub / In-process / 临时 HTTP Server |
| 离线 Agent 评测 | stdio(Fixture Server) |
| 本地轻量开发 | stdio |
| VM Smoke / VM Release / VM 演示(标准部署) | **Streamable HTTP** |

- **零 direct bypass**:HTTP 网络错误后不静默回退 stdio 或 direct(`direct_fallback=false` 恒成立);stdio 不是运行失败后的备用路径。
- `execute_tool` 的 `transport` 审计字段新增 `mcp_streamable_http`(与 `mcp_stdio` / `legacy_direct` / `internal_control` 区分)。
- **full_e2e/production Profile 禁止 Spawn MCP stdio 子进程**,启动时断言 `transport == streamable_http` 且无 stdio 子进程。

## 3. 核心不变量(8 条)

1. 工具实现唯一,Transport Adapter 不包含业务逻辑。
2. 标准部署只走 Streamable HTTP,网络失败不降级。
3. AI 服务不持有调查凭据,MCP 服务不持有处置凭据。
4. 模型只生成业务工具参数,调查与审计上下文由程序注入。
5. 根因 Policy、审批、处置和恢复判定仍由 AI 控制服务负责。
6. MCP 服务只提供七个只读事实采集工具。
7. stdio 仅用于本地和离线评测,不是生产故障回退路径。
8. HTTP 服务保持无状态,支持独立部署和水平扩展。

## 4. 协议版本与会话模型

- **无会话、无服务端跨请求状态**的 Streamable HTTP(7 个调查工具均为单次请求,可无状态);SDK 侧启用 `stateless_http=True`(`StreamableHTTPServerTransport(mcp_session_id=None)`)。多个实例可水平扩展,不做会话亲和。
- 冻结版本常量进 `app/mcp/contract.py`,契约校验扩展:
  - `MCP_PROTOCOL_VERSION`:运行时协商并校验(`SUPPORTED_MCP_PROTOCOL_VERSIONS` / `EXPECTED_MCP_PROTOCOL_VERSION`),初始化协商得到 `negotiated_protocol_version`,后续请求使用协商结果;
  - `MCP_SDK_VERSION`:构建产物实际安装版本,由 `importlib.metadata` 读取,与 Build Manifest 不一致则启动失败,**不作为可修改环境配置**(环境变量无法改变已安装 SDK,只能伪造字符串);
  - `MCP_TOOL_CONTRACT_VERSION`:既有应用级契约版本;
  - `INVOCATION_CONTEXT_VERSION`:新增,上下文载体格式版本。
- 四个版本维度(Tool Schema / MCP Protocol / SDK Version / InvocationContext)相互独立。

## 5. 代码结构与依赖边界

Monorepo 内(不拆仓库、不发布独立 Python Package),但建立代码依赖边界:

```
ai-service/app/
├── tools_core/                     # 传输无关核心,禁止反向依赖 AI 应用层
│   ├── registry.py                 # ToolRegistry(7 工具声明)
│   ├── schemas.py                  # 工具参数/结果 Schema(strict, extra=forbid)
│   ├── context.py                  # InvocationContext 定义
│   ├── service.py                  # ToolExecutionService(参数校验/上下文校验/执行/审计)
│   ├── ports.py                    # 端口接口(见下)
│   ├── errors.py                   # 工具业务层错误码 + retryable
│   └── handlers/                   # 7 个调查 handler(service_metrics/trace/query_digest/query_plan/index_info/lock_waiters/transaction_details)
├── tools_infrastructure/           # 适配器(基础设施实现)
│   ├── mysql.py  prometheus.py  jaeger.py  java_observation.py  audit_repository.py
└── mcp/
    ├── server_factory.py           # create_mcp_server(runtime) 工厂
    ├── server_stdio.py             # --transport stdio CLI
    ├── server_http.py              # ASGI App + Uvicorn 入口(安全中间件链)
    ├── client.py                   # McpClientManager(双 transport 统一入口)
    ├── client_transport_stdio.py   # stdio Adapter
    ├── client_transport_http.py    # Streamable HTTP Adapter(逐请求 Headers)
    ├── security.py                 # 认证中间件(Opaque Token→Principal)+ 限流 + Origin
    ├── protocol_errors.py          # 协议/上下文层错误
    ├── client_errors.py            # 连接/超时/认证/重试映射
    └── contract.py                 # 版本常量 + 契约校验
```

**ports.py 定义**(工具核心只依赖这些接口):Incident/Run 查询;Tool Audit Writer;MySQL 调查接口;Prometheus Client;Jaeger Client;Java Observation Client。

**tools_core 禁止导入**:`agent`、`langgraph`、`llm`、`prompt`、`fix_executor`、`session_terminator`、`FastAPI`、`FastMCP`。用**导入边界测试**(黑名单断言)静态保证,不只依赖开发者自觉。

**依赖方向**:`agent/llm → mcp/client → tools_core contracts`;`mcp/server → tools_core service → ports → tools_infrastructure`。

## 6. Server 工厂与双入口

- 统一工厂:`create_mcp_server(runtime: ToolRuntime) -> FastMCP`;stdio 与 HTTP 是**不同进程中的独立 FastMCP 实例**(不共享内存实例),但同一工厂、同一 ToolRegistry、同一 Fixture 语义。
- 入口:
  - `python -m app.mcp.server --transport stdio`(本地/评测,现状保留);
  - `python -m app.mcp.server --transport streamable-http`(独立容器,`stateless_http=True`)。
- 每次测试创建新实例,避免 ToolRegistry / Fixture / 限流状态在测试间污染。
- **Fixture 通过依赖注入创建**:`create_real_tool_runtime()` / `create_fixture_tool_runtime(fixture_file)`;stdio 离线评测可建 Fixture Runtime;HTTP 协议集成测试用 Test Profile 建 Fixture;full_e2e/production 只能 Real Runtime,检测到 Fixture 配置直接拒绝启动;标准 HTTP 镜像不包含 Fixture 文件;审计标记 `fixture=false`。

## 7. 安全边界

### 7.1 凭据所有权(compose 层强制执行)

| 服务 | 持有凭据 |
|---|---|
| ai-service | LLM key、控制库(现状维持)、审批/Action 调度 |
| mcp-tools | `ai_investigator`(只读)、Prometheus、Jaeger、Java 只读端点、`mcp_tool_auditor`(最小审计写) |
| 禁入 mcp-tools | LLM key、fix_executor、session_terminator、业务写账号、control_app 完整权限 |
| 禁入 ai-service(标准 HTTP 模式) | 调查数据库凭据(不绕开 MCP 直接调查) |

### 7.2 认证模型(Opaque Token,不做 JWT/OAuth)

- **方案一(Opaque Token)**:客户端 `TRACEMIND_MCP_HTTP_BEARER_TOKEN`;服务端 `TRACEMIND_MCP_AUTH_CLIENTS_FILE`(Token Fingerprint → Principal + Scopes 映射,如 `{subject: ai-service, audience: tracemind-mcp-tools, scopes: [tools:investigate]}`)。
- 服务端只保存 Token Fingerprint(或 Secret 注入);常量时间比较;支持 active/next 双 Token 配置(运维切换,不自动轮换)。
- 日志不得输出 Authorization Header;健康检查不得验证或显示 Token;审计只保存 `client_id / token_fingerprint`。
- **client_id 只能由认证结果派生**(`Authorization → AuthenticatedPrincipal.subject → client_id`),不接受请求传入的 client_id;若请求携带与认证主体不一致的 client_id → 拒绝。
- **不声称实现了 MCP OAuth Authorization**(内部服务身份认证 ≠ 面向第三方用户的 OAuth)。

### 7.3 InvocationContext 载体与校验

- **Client 只注入**:`incident_id`、`agent_run_id`、`tool_call_id`、`purpose`(X-TraceMind-Incident-Id / X-TraceMind-Agent-Run-Id / X-TraceMind-Tool-Call-Id 受控 Header);`traceparent`/`tracestate` 为 W3C Transport Trace Context Header。
- 这些字段**不出现在 tools/list 的模型可见 Schema**,不进 LLM Prompt;有长度/格式/类型限制;Server 端重新校验;不允许从 Tool Arguments 覆盖。
- **伪造字段 = 拒绝请求**(非静默剔除):业务参数 `strict schema + extra=forbid`;出现 reserved context field → `MCP_CONTEXT_SPOOFING_REJECTED`;不执行工具、不产生 Evidence;保留安全审计;错误响应不回显伪造值。
- 按最新 MCP 协议规范校验 `MCP-Protocol-Version` / `Mcp-Method` / `Mcp-Name` Header 与 JSON-RPC Body 中方法/工具名的一致性,不一致拒绝。

### 7.4 Context 拆分

- `ClientInvocationContext`:incident_id / agent_run_id / tool_call_id / purpose。
- `AuthenticatedPrincipal`:client_id / subject / audience / scopes / token_fingerprint(**Client 无权构造**)。
- `ServerInvocationContext`:ClientInvocationContext + AuthenticatedPrincipal + trace_context + protocol_version + mcp_request_id。

### 7.5 Purpose 与 Run 状态白名单

`purpose` 由程序注入(`investigation | recovery_verification`),模型不能设置;Server 联合 Purpose + Run 状态判定:

| 调用目的 | 允许状态 |
|---|---|
| 证据收集 | running / collecting / diagnosing |
| 恢复验证的数据采集 | verifying_recovery |
| 审批等待 | 默认禁止 |
| 已终止 / 被拒绝 / 历史回放 | 禁止 |

校验:Incident 存在;Agent Run 存在;`agent_run.incident_id == incident_id`;Run 未终止;Tool Call 归属当前 Run;Client 被允许访问该 Run。

### 7.6 HTTP 端点与网络边界

- `POST /mcp`(Streamable HTTP 请求);`GET /mcp`(无会话按 SDK 行为返回 405);`GET /health/live`(进程与事件循环存活);`GET /health/ready`(ToolRegistry + 认证配置 + 审计库可用,**不依赖下游 MySQL/Prometheus/Jaeger/Java 全部健康**;下游经内部依赖探针分别检查;响应只返回状态与版本,不返回 DB 地址/账号/Token/详细异常)。
- mcp-tools:8001 **仅暴露 Compose 内部网络**,不映射宿主机端口;AI Service 经内部 DNS 访问;跨主机部署必须 HTTPS;Compose 内部 HTTP 是明确接受的受控例外。
- **Origin 校验**(SDK `transport_security` 原生支持 DNS Rebinding 防护):Origin 缺失 → 认证通过后允许继续(服务间调用);Origin 存在 → 必须命中精确 Allowlist;禁止 `*`;禁止按 Host 动态放行;不启用宽泛 CORS;浏览器直连 V1.7 不支持。
- URL 只能来自服务端配置,不能由 Incident / 前端 / LLM 传入。
- **请求/结果大小分限**:`TRACEMIND_MCP_MAX_REQUEST_BYTES=262144`(256 KiB)/ `TRACEMIND_MCP_MAX_RESULT_BYTES=1048576`(1 MiB);禁止/限制压缩请求防解压放大;列表工具限制最大返回条数;超限返回 `RESULT_TOO_LARGE`,不截断伪装成完整证据,标记 Evidence 不可用。
- **速率限制两层**:认证前按来源地址粗粒度(防暴力尝试);认证后按 client_id + tool_name;并发:全局 + 单 Client 最大 In-flight;429 带 Retry-After。**单实例限流,不承诺全局精确配额**(多实例全局配额留未来 Gateway/Redis)。

### 7.7 错误三层分层

| 层 | 码 | 语义 |
|---|---|---|
| HTTP 安全层 | 401 未认证/Token 无效;403 Scope 不足/Origin 拒绝;413 请求体过大;415 Content-Type 不支持;429 限流 | 错误正文不泄露 Token / 允许 Origin / 内部账号 / 权限细节 |
| MCP/JSON-RPC 协议层 | 协议版本不支持;Header/Body 不一致;JSON-RPC 格式错误;Tool 不存在;Tool Schema 不匹配 | 用协议规定的响应语义 |
| 工具业务层 | 受控 CallToolResult:`{isError: true, structuredContent: {errorCode: "TRACE_NOT_FOUND", retryable: false}}` | Client 统一映射到内部错误码,再决定是否重试 |

错误模块按层拆分:`tools_core/errors.py`(ToolBusinessError)、`mcp/protocol_errors.py`(Protocol/Schema/Context)、`mcp/client_errors.py`(Connect/Timeout/Auth/Retry 映射)。避免把"业务事实缺失"和"基础设施故障"塞进同一枚举。

### 7.8 重试语义

- 区分 **Logical Tool Call**(`tool_call_id`,重试保持不变)与 **Attempt**(`attempt_no`,每次 HTTP 尝试递增;`mcp_request_id`,每个实际 HTTP 请求唯一;**attempt_no 由 Server 原子分配**,不信任请求端传入)。
- 规则:最大 **3 次**尝试含首次;指数退避 + Jitter;429 尊重 Retry-After;连接失败 / 502 / 503 可重试;504 / 读取超时按 `outcome_unknown` 处理再按策略决定;401 / 403 / 400 / 404 / 413 / Schema / Context 错误**不重试**;普通 500 默认不重试,除非服务明确 `retryable=true`;每次尝试计入 Agent 工具预算;整个逻辑 Tool Call 受总 Deadline 限制。
- **超时 ≠ 工具未执行**:审计必须能表示 `outcome_unknown`;Client 超时但 Server 继续执行时,Client 侧标记 `outcome_unknown`;Server 后续完成只能补充审计,不能自动成为 Agent Evidence。
- 重试重新采集的只读结果必须带新的 `observed_at`。
- 任何错误**禁止回退 stdio / direct**。

## 8. 配置(按进程拆分 Settings, fail-closed)

- `CommonSettings` / `McpClientSettings` / `McpHttpServerSettings` / `McpStdioServerSettings` / `AiServiceSettings`(模块化,`app/config/` 下拆分)。
- **进程入口显式构建对应配置,模块 import 不自动实例化全部 Settings**——避免 mcp 容器被迫要求 LLM key、AI 容器被迫要求调查凭据;"禁入 mcp-tools 的凭据根本不需要存在"。
- 配置项:
  - `TRACEMIND_MCP_TRANSPORT = stdio | streamable_http`
  - `TRACEMIND_MCP_HTTP_URL` / `TRACEMIND_MCP_HTTP_BEARER_TOKEN` / `TRACEMIND_MCP_HTTP_CONNECT_TIMEOUT_SECONDS` / `TRACEMIND_MCP_HTTP_REQUEST_TIMEOUT_SECONDS` / `TRACEMIND_MCP_HTTP_MAX_RETRIES`
  - `TRACEMIND_MCP_AUTH_CLIENTS_FILE`(Server 侧)
  - `TRACEMIND_MCP_MAX_REQUEST_BYTES=262144` / `TRACEMIND_MCP_MAX_RESULT_BYTES=1048576`
  - `TRACEMIND_MCP_PROTOCOL_VERSION`(协议版本,代码常量 + 协商)
  - `TRACEMIND_MCP_HTTP_BEARER_TOKEN`(Client 侧)
- 校验规则:stdio 需要 Server Command,不需要 URL/Token;streamable_http 需要 URL + 凭据;`full_e2e`/`production` **禁止选择 stdio**;跨主机 URL 禁止明文 HTTP;HTTP 配置错误启动失败;**不允许自动切换 Transport**。

## 9. 审计与数据库账号

### 9.1 审计唯一所有者(避免 Client/Server 双落)

| 方 | 记录 |
|---|---|
| AI Service | LLM 为什么选择该工具;逻辑 `tool_call_id`;Agent Round;业务参数摘要;Evidence 如何消费结果 |
| MCP Server | 认证结果;HTTP/MCP 请求;实际执行 Attempt;Transport;工具耗时;Tool Result/错误;Server Instance + Trace ID |

表结构与唯一约束:

- `tool_call`:逻辑工具调用。`UNIQUE(agent_run_id, tool_call_id)`
- `tool_call_attempt`:传输与执行尝试。`UNIQUE(tool_call_id, attempt_no)`、`UNIQUE(mcp_request_id)`;字段含 `started_at / completed_at / outcome / protocol_version / transport / server_instance_id / trace_id / request_hash / result_hash / error_code / retryable / latency_ms`
- 不存完整 Token、Header 或未脱敏原始请求。

### 9.2 两段式审计(fail-closed)

1. 写 `started` → 执行只读工具 → 写 `completed` / `failed`。
2. `started` 无法落库 → **不执行工具**,返回 `MCP_AUDIT_UNAVAILABLE`;
3. 工具完成但终态审计失败 → **不把结果作为有效 Evidence**,返回 `MCP_AUDIT_PERSIST_FAILED`;
4. 服务端保留脱敏应急日志;不伪造 completed;后续完整性检查可标记 `outcome_unknown`。

### 9.3 账号 Provisioning(与 Migration 分离)

- **Migration(版本化 SQL)**:新增 `tool_call_attempt` 表、索引/约束;新增角色与权限结构。**不含环境密码**(避免 V1.6 已修复的"环境密码进入版本化 SQL"问题)。
- **Account Provisioning(运行时 Secret)**:从运行时 Secret 创建/更新 `mcp_tool_auditor` 密码;绑定最小权限 Role。
- `mcp_tool_auditor` 权限:SELECT incident / agent_run;INSERT/UPDATE tool_call / tool_call_attempt / observation_query;禁止修改 incident / hypothesis / evidence / approval / fix_execution;不允许 DDL / KILL;不能读取 LLM 调用审计中的敏感内容。
- 权限探针(验收时):能查询 Incident/Run;能写 Tool Attempt;不能修改 Incident;不能写 Evidence/Approval/FixExecution。

## 10. 部署(Docker 双 Target + Compose 三网络)

### 10.1 Dockerfile 双 Target

同一仓库、同一源码、不同 Target:

```
ai-service/Dockerfile
  target=ai-runtime        → tracemind-ai-service:<commit>
  target=mcp-tools-runtime → tracemind-mcp-tools:<commit>
```

MCP 镜像**不安装 LLM/Agent 运行依赖**(按 target 分层安装依赖);两个镜像独立发布/回滚。V1.7 不做 CI 构建产物发布,仅 VM 本地 build 两镜像,tag 用提交短 hash(裁剪:不做独立 Python Package、不做 CI 制品仓)。

### 10.2 Compose 三网络

```
agent-mcp-network       ai-service, mcp-tools
control-data-network    ai-service, mcp-tools, mysql
tool-observation-network mcp-tools, mysql, prometheus, jaeger, order-service, inventory-service
```

- 标准 HTTP 模式:AI Service 不加入 tool-observation-network;mcp-tools 不加入 LLM 出口网络、不映射宿主机端口;Compose Health Check 在容器内部访问 `/health/ready`;禁用隐式 Default Network。
- **如实说明**:控制库与业务库仍在同一 MySQL 实例,网络无法隔离同实例内的 Schema,最终权限仍由数据库账号保证。

## 11. 验收体系(verify-m17 三层)

统一入口 `scripts/verify-m17.py`(Python 编排,跨 Windows/VM;PowerShell 仅留轻量包装 `verify-m17.ps1`)。**人工触发,但内部自动执行全部步骤并生成 JSON 汇总**,不要求手动跑十几个命令。

### 11.1 第一层:Local Fast Regression(`--tier fast`)

- 环境:Windows 本地;FakeLLM;Fixture;stdio 与临时 HTTP Server;不调用真实模型;不要求启动完整 Compose。
- 内容:tools_core 单元测试;工具导入边界测试;七工具 Contract/Schema Hash;stdio/HTTP Adapter 一致性;HTTP 认证与安全测试;InvocationContext 并发隔离;HTTP 错误映射与重试;Tool Call/Attempt 审计测试;动态 N/N 离线 Agent 评测;Replay 对新 Transport 枚举兼容;ai-service 全量 pytest。
- Java / Vue 回归列为**可选项**(手动按需,默认不跑,保持快)。

### 11.2 第二层:VM Standard Smoke(`--tier vm-smoke`)

- 环境:VM;Docker Compose;独立 mcp-tools 容器;Streamable HTTP;真实 MySQL/Prometheus/Jaeger/Java;FakeLLM 或少量真实模型调用。
- 内容:mcp-tools 独立容器健康;AI Service 用 Streamable HTTP;无 Spawn stdio 子进程;七工具均可经 HTTP 调用;HTTP 认证/Origin/协议版本校验生效;审计 `mcp_streamable_http`;`direct_fallback=false`;SCN-001/SCN-002 各完成一次闭环;Replay Backend 正确;凭据/网络/数据库权限隔离通过。

### 11.3 第三层:VM Release(`--tier release`)

- 环境:real_strict;degraded=false;Streamable HTTP;真实基础设施。
- 最低标准:真实模型冒烟成功;SCN-001 ≥1/1;SCN-002 ≥1/1;无 stdio/direct 降级;所有调查工具走 Streamable HTTP;审批/处置/恢复验证闭环成功;生成完整但脱敏的发布报告。
- 额度充足可提高为 SCN-001/002 各 3/3,但个人项目不必每次修改都跑 3/3。
- **额度提醒**:release 层遇 429 / 额度不足立即停下,告知用户更换模型后继续。

### 11.4 远程服务故障用例(V1.7 核心证明点)

- 停止 mcp-tools → Agent 收到 `MCP_CONNECT_FAILED` → 不回退 stdio/direct → Incident 进入 needs_human 或受控失败 → 不生成虚假 Evidence;恢复 mcp-tools → 新调查正常执行。
- Prometheus 不可用,不影响 MySQL 工具本身;Jaeger 不可用,不影响锁调查工具;审计数据库不可用时工具执行 fail-closed;网络超时不被错误解释为"查询没有结果"。

### 11.5 凭据隔离验收(不输出完整环境)

验收脚本只输出布尔结果,不运行/保存完整 `docker inspect` / `docker exec env`:

```json
{ "aiServiceForbiddenCredentialsPresent": false, "mcpToolsForbiddenCredentialsPresent": false }
```

### 11.6 发布报告(主要工程证据,无 CI 后尤其重要)

`reports/generated/v1.7/`:`validation-summary.json/.md` + `test-results/` + `evaluation-results/` + `sanitized-logs/`。

记录:Git Commit SHA;Git Tag;执行时间与环境;模型名称/快照;Prompt/Policy/MCP Contract 版本;MCP Protocol 与 SDK 实际版本;Invocation Context 版本;MCP Server 镜像 Digest;七工具 Contract Hash;离线评测 N/N;SCN-001/002 结果;逻辑 Tool Call 数;HTTP Attempt 与重试数;`directFallback=false`;凭据隔离结果;各阶段耗时与失败分类。

**不全量提交 git**:原始报告只留本地/VM;脱敏摘要复制 `docs/releases/v1.7-validation-summary.md` 提交作为版本证据。

## 12. 术语与 GitHub 职责

- **术语**:Fast/Full CI → `Local Fast Regression` / `VM Release Validation` / `Validation Report` / `Release Acceptance Gate` / `Validation Run`。"Gate"现在是**项目流程约定**,不是 GitHub 强制门禁:只有 VM Release Validation 通过后,才允许创建 V1.7 版本 Tag。
- **GitHub 职责收敛**:仅远程代码备份;提交历史;版本 Tag;README 与设计文档展示;可选手动创建 Release;展示脱敏版本验收摘要。不再承担 Actions CI / 自动质量门禁 / 真实模型验收 / 容器化 Full E2E / 自动发布。
- README 不显示 CI Badge,不写"每次提交自动回归";表述为"项目提供本地快速回归与 VM 分层发布验收脚本,所有真实模型与故障闭环验收由开发者显式触发"。

## 13. 范围边界(YAGNI)

- 不做 GitHub Actions 或其他持续集成平台。
- 不做 MCP OAuth 或第三方正式开放接入(V1.7 只正式授权 TraceMind AI Service)。
- 不做 JWT,使用内部 Opaque Token。
- 不做跨实例全局限流(单实例限流;多实例全局配额留 Gateway/Redis)。
- 不实际部署多实例,只保证无状态扩展基础。
- 不做完整 MCP OTel Span 链路采集,只传播 `traceparent` 并写审计字段(client_id/transport/mcp_request_id/tool_call_id/trace_id/latency_ms/auth_result/protocol_version);完整链路(V1.8:C 方向 Exemplars + 多 DB span + MCP 调用链)留后续。
- 不做高并发压测,只记录单实例功能与基础延迟。
- 不做 Token 自动轮换(提供 active/next 双 Token 配置字段,运维切换)。

## 14. 最终验收矩阵

| 层级 | LLM | MCP 传输 | 数据源 | 触发 |
|---|---|---|---|---|
| 本地 fast | Fake | Stub/HTTP 临时服务 | Fixture/测试库 | 手动一键 |
| 本地离线评测 | Fake | stdio | Fixture | 手动一键 |
| VM Smoke | Fake 或少量 Real | Streamable HTTP | 真实基础设施 | 手动一键 |
| VM Release | real_strict | Streamable HTTP | 真实基础设施 | 正式发版前 |
| VM 演示 | real_strict | Streamable HTTP | 真实基础设施 | 面试演示 |

## 15. 手工配置说明

- `ai-service` 与 `mcp-tools` 密钥走 VM 本地 `.env.vm`,不进 git。
- `TRACEMIND_MCP_HTTP_BEARER_TOKEN`(Client)与 `TRACEMIND_MCP_AUTH_CLIENTS_FILE`(Server)由部署时配置;Token 不进入 LLM Prompt / Tool Schema / 审计正文 / 日志。
- mcp-tools 容器不注入:LLM key、fix_executor、session_terminator、业务写账号、control_app 完整权限。
