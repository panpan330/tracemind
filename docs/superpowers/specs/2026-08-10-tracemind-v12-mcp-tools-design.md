# TraceMind V1.2 设计:工具层 MCP 化(stdio)

> 状态:设计定稿(经分段评审,用户 3 批共 21 条建议评估后采纳)。
> 前置:V1.1 已完成并验收(真实 LLM + Tool Calling + RAG + 评测体系,全栈闭环 3/3)。

## 1. 背景与目标

V1.1 的 Agent 通过**进程内直接调用**(`execute_tool`)执行五个只读调查工具。V1.2 将其升级为标准 **MCP(Model Context Protocol)工具服务**:Agent 经 stdio MCP 协议调用工具,消除调查工具的 direct 路径,使工具契约与 LangGraph 解耦、可被标准 MCP 客户端发现调用。

## 2. 范围与关键决策(已确认)

- **方向**:MCP 工具服务
- **模式 A**:工具层变 MCP Server(AI 服务作为 MCP Client)
- **传输**:stdio(MCP Server 为 ai-service 同环境子进程)
- **改造深度**:完全走 MCP——五个 Agent 只读调查工具全部经 stdio MCP 调用,**不保留 direct 路径**;`execute_fix` / `verify_recovery` 属确定性安全控制节点,**不纳入 MCP 工具集合**

## 3. 架构总览

```
┌──────────────────────── ai-service 容器 ────────────────────────┐
│  LangGraph Agent                                                │
│   collect_evidence ──► McpClientManager.call_tool (同步桥接)     │
│                            │ run_coroutine_threadsafe            │
│                            │ 专用后台线程 + 唯一 asyncio loop     │
│   spawn: python -m app.mcp.server ──► 子进程                      │
│                            │ stdio JSON-RPC                      │
│                        MCP Server (FastMCP)                     │
│                        5 个只读调查工具                            │
│                            │                                     │
│                        execute_tool(复用)                        │
│                        TOOL_REGISTRY + schema + 审计 + fixture   │
└──────────────────────────────────────────────────────────────────┘
```

- MCP Server 与 ai-service **同一代码仓库和运行环境,运行时为独立子进程**
- `execute_tool`、TOOL_REGISTRY、参数校验、审计核心逻辑**继续复用**
- Fixture 匹配逻辑保留,**注入方式改为 MCP Server 启动配置**

## 4. MCP Server 设计(`app/mcp/server.py`)

- 依赖锁定:`mcp>=1.28,<2`(提交 `uv.lock`;VM 构建用 `uv sync --frozen`;阿里云镜像缺锁定版本时回退官方 PyPI;不在部署时临时更新依赖)
- 用官方 SDK `FastMCP("tracemind-tools")`
- 暴露 5 个只读调查工具(与 V1.1 LLM 可调用集合一致):

| MCP tool 名 | 参数(显式类型签名,FastMCP 生成 JSON Schema) |
|---|---|
| `get_service_metrics` | `incident_id: int, service_ref: str, window_seconds: int` |
| `get_trace` | `incident_id: int, trace_id: str` |
| `list_expensive_query_digests` | `incident_id: int, window_seconds: int` |
| `get_query_plan` | `incident_id: int, query_ref: str, sample_parameters: dict` |
| `get_index_info` | `incident_id: int, table_ref: str` |

- **每个工具用显式参数签名**(不用 `**params`,FastMCP 需可生成 JSON Schema),内部一行委托:`return execute_tool(name, incident_id=incident_id, agent_run_id=agent_run_id, **business_params)`
- **上下文与业务参数分离**:`incident_id` / `agent_run_id` 由 MCP Client 注入,不传给 LLM、不参与 Fixture 参数哈希、不传给具体业务 Tool Handler,仅用于审计与关联
- **Server 校验**:Incident 存在、AgentRun 存在、`AgentRun.incident_id == incident_id`
- **tools/list 是协议内置能力**,不注册额外 tool;外部演示客户端另起 stdio 子进程,不连接 ai-service 已占用的会话
- **stdout 纯净**:仅 MCP JSON-RPC 消息;日志/错误走 stderr;禁止 print 进 stdout(设计 + 测试标准)

## 5. MCP Client 与生命周期(`app/mcp/client.py`)

### 5.1 McpClientManager(同步桥接)

```
McpClientManager
├─ 独立后台线程
├─ 线程内持有唯一 asyncio event loop
├─ stdio ClientSession 始终属于该 event loop
├─ 同步 call_tool 经 run_coroutine_threadsafe 提交
└─ FastAPI lifespan 负责启动和关闭
```

**禁止**:每次调用 `asyncio.run()`;跨事件循环复用 ClientSession;每次工具调用重新创建子进程;只依赖 atexit 清理。

### 5.2 生命周期(生产)

- **FastAPI lifespan startup**:spawn MCP Server → initialize → `tools/list` 契约校验 → readiness=true → 持续复用会话
- **shutdown**:关闭 MCP Session → 等待子进程退出 → 超时终止
- `get_mcp_client()` 仅取已初始化实例,**业务调用期间不悄悄启动进程**

### 5.3 离线评测生命周期

- 不经过 FastAPI lifespan,用显式上下文:`McpClientManager(fixture_file=case.json)` → 启动 Server → 跑图 → 关闭 Server
- atexit 仅作异常兜底

### 5.4 契约校验(tools/list)

校验工具名称集合**完全一致**、不包含 `execute_fix` / `verify_recovery`、每个工具必需参数/类型/枚举/边界一致、`tool_schema_version` 一致——防止 Schema 漂移通过启动检查。

## 6. 执行上下文与审计

- `agent_run_id` 作为**执行上下文**:Client 注入、不传 LLM、不参与 Fixture 参数哈希、不传业务 Handler,只用于审计与关联
- `record_tool_call` 与 `tool_call` 表支持 `agent_run_id`
- 调用链:`LLM 生成 {service_ref, window_seconds}` → MCP Client 注入 `{incident_id, agent_run_id}` → Server 校验 → `execute_tool(name, incident_id, agent_run_id, **business)`

## 7. Fixture 模式(离线评测隔离)

- `--fixture-file` 仅 `TRACEMIND_EVAL_MODE=true` 时允许
- Fixture 文件必须位于配置的评测目录
- **synthetic context**:跳过 Incident/AgentRun 校验,不访问真实工具,不写正式业务审计
- Fixture 未命中立即返回 `FIXTURE_NOT_FOUND`,禁止未命中后调用真实基础设施
- 子进程重启后仍携带同一 Fixture 文件
- 离线评测结果单独写评测报告,不依赖创建真实 Incident/AgentRun

## 8. 错误处理与重试

**错误码(至少区分)**:

| 错误码 | 场景 |
|---|---|
| `MCP_START_FAILED` | 子进程启动失败 |
| `MCP_SCHEMA_MISMATCH` | tools/list 契约不一致 |
| `MCP_TIMEOUT` | 工具调用超时 |
| `MCP_DISCONNECTED` | 会话断开 |
| `MCP_PROTOCOL_ERROR` | 非法协议响应 |
| `MCP_TOOL_ERROR` | 工具自身失败(业务) |

**重试规则**:
- Server 启动失败:重启一次
- 初始化失败:重启一次
- 会话在调用前断开:重启后重试一次
- 请求已发送但结果未知(只读工具可重试):使用同一 `mcp_invocation_id` 关联两次执行记录
- 第二次仍失败:返回结构化失败,**不降级 direct**

## 9. 数据库迁移(版本化 SQL)

`tool_call` 表新增字段(版本化迁移脚本,非应用启动随意 ALTER):

| 字段 | 定义 |
|---|---|
| `agent_run_id` | `BIGINT NULL`(建索引;按现有数据关系决定外键) |
| `transport` | `VARCHAR(32) NOT NULL DEFAULT 'legacy_direct'` |
| `mcp_request_id` | `VARCHAR(64) NULL` |
| `mcp_attempt` | `INT NULL` |

**transport 取值**:
- `legacy_direct`:V1.1 历史调查调用
- `mcp_stdio`:V1.2 五个调查工具
- `internal_control`:execute_fix / verify_recovery(确定性安全控制节点)
- `fixture_mcp_stdio`:可选,仅评测报告使用,不一定落业务库

`mcp_request_id` 用于关联断线重试产生的多次执行尝试。

## 10. 测试策略(分层)

| 层 | 内容 |
|---|---|
| MCP Server 单元测试 | 工具注册(5)、委托 execute_tool、Fixture 加载、上下文校验 |
| MCP Client 单元测试 | Stub Session 封装、超时、错误转换 |
| MCP 协议集成测试 | 真实 stdio 子进程:initialize / tools/list / tools/call / 重启 / **stdout 纯净** |
| Agent 单元测试 | 经 tools/Gateway 注入 Stub,不启动子进程 |
| 离线评测 | 每条 Case 真实 stdio MCP Server + Fixture 文件 |
| 全栈回归 | 真实五工具断言 `transport=mcp_stdio` |

## 11. 部署与交付

- 开发:本地双进程(ai-service + spawn 的 MCP Server 子进程)
- 交付:同容器,MCP Server 由 ai-service 进程内 spawn,compose **零新增服务**
- Fixture 模式:`python -m app.mcp.server --fixture-file <case.json>`(需 `TRACEMIND_EVAL_MODE=true`)
- VM 构建:阿里云 pip 源 + `uv sync --frozen` + legacy builder

## 12. 验收清单

### 正常路径

- `pytest` 全绿(Server/Client/协议集成/Agent 单测)
- fake 离线评测 16/16,**无 MCP 基础设施错误**
- real_strict 评测无 MCP 基础设施错误
- e2e-scn001-real 3/3 无 MCP 基础设施错误
- **禁止静默回退 direct**

### 故障路径(主动注入并断言)

| 注入 | 断言 |
|---|---|
| Server 无法启动 | `MCP_START_FAILED` |
| 工具 Schema 不一致 | `MCP_SCHEMA_MISMATCH` |
| 工具调用超时 | `MCP_TIMEOUT` |
| 子进程中断 | `MCP_DISCONNECTED` |
| 非法协议响应 | `MCP_PROTOCOL_ERROR` |
| 工具自身失败 | `MCP_TOOL_ERROR` |
| 重启超过一次 | 明确失败 |

### 传输断言

- 五个调查工具:`transport=mcp_stdio`
- `execute_fix`:`transport=internal_control`
- `verify_recovery`:`transport=internal_control`
- 消除五个调查工具的 direct 路径;安全控制节点继续使用内部确定性调用

### 评测报告需证明真的走过 MCP

Fixture 调用若不落业务审计表,评测报告须记录:transport=mcp_stdio、MCP Server PID、协商的协议版本、工具调用次数、工具名称、MCP 基础设施错误次数、是否发生 direct fallback。

## 13. 范围外(明确不做)

- 不把 execute_fix / verify_recovery 暴露为 MCP(写路径 + 审批唯一性)
- 不做 MCP HTTP/SSE 传输(stdio 闭环,HTTP 留后续)
- 不做 LangGraph 整体异步化(同步桥接已覆盖 session 复用)
- 不做外部 MCP 客户端接入协议细节(演示仅 stdio demo 脚本)

## 14. 简历叙事

> 将五个只读调查工具封装为基于官方 SDK 的 stdio MCP Server,LangGraph Agent 通过持久化 MCP Client 会话完成工具发现和调用,消除了调查工具的进程内直连路径。审计上下文由程序注入,不受模型控制;写操作与恢复判定继续留在确定性安全控制面。通过真实 stdio Fixture 评测和 SCN-001 三轮全栈回归验证协议链路、审计归属及故障恢复能力。工具契约与 LangGraph 解耦,可被标准 MCP 客户端在 TraceMind 运行环境中发现和调用。
