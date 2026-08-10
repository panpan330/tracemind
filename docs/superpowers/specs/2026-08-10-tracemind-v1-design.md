# TraceMind V1.0 设计文档

- 日期:2026-08-10
- 状态:已与需求方确认,待实施
- 范围:V1.0 核心故障闭环(含 Vue 工作台);V1.1 扩展方向见文末

## 1. 项目概述

TraceMind 是一个面向微服务系统的 AI 故障诊断与安全处置平台:接收系统故障事件,由 AI Agent 主动调用受控工具收集监控、调用链、慢 SQL、执行计划与索引元数据等证据,基于证据判定根因,提出预定义修复方案,在人工审批后执行修复,并验证系统是否真正恢复,最后生成完整复盘报告。

本项目为求职作品集项目,按真实工程标准设计开发。核心叙事(面试讲解主线):

1. **证据驱动的根因判定**——根因与恢复结论由程序规则闸门确定,LLM 只负责提出假设、选择工具与解释证据,不允许仅凭猜测下结论。
2. **人机协同的安全闭环**——Agent 永远无法执行任意 SQL/Shell;修复动作必须预定义、经人工审批、校验审批绑定后执行;审批前状态机挂起,审批后恢复。
3. **全链路审计可回放**——每次工具调用、假设变化、审批决策、修复执行均落库,配合 `incident_event` 事件流,调查全过程可展示、可复盘、可回放。

## 2. 范围与版本边界

### V1.0(本设计文档主体)

- 唯一故障场景:库存查询缺少联合索引 → 慢 SQL → 库存服务接口响应时间升高(SCN-001)。
- 两个独立 Java 微服务(order-service、inventory-service),真实跨服务调用与 traceId 关联。
- 真实 MySQL 8:业务数据、`performance_schema`、`information_schema`。
- Python AI 服务:LangGraph 显式图编排、七受控工具、证据链、规则闸门、人工审批中断/恢复、恢复验证、复盘报告。
- Vue 3 工作台(三个页面)实时展示调查过程并承载审批。
- 测试、审计、四账号三连接池权限隔离、本地开发脚本 + M5 Docker Compose 交付。

### V1.1(扩展方向,不在本实施计划内)

Qdrant + Runbook RAG 知识检索、Agent 评测数据集、调查回放、回归评测。

### 明确不做(避免范围蔓延)

- 不做完整商城业务、不做用户注册/OAuth/复杂 RBAC(V1.0 为本地演示环境,不宣称生产级认证)。
- V1.0 不引入 redis / qdrant / OpenTelemetry / Prometheus / MCP(场景用不到;链路升级、RAG、MCP 分属 V1.1/V1.2)。
- 不做通用聊天窗口、可拖拽 Agent 工作流、大型监控看板。

## 3. 架构总览

```
┌────────────────────────────────────────────────────────────────┐
│                  Vue 3 工作台 (dev:5173 / prod:8080)             │
│   场景与事件列表 · Incident 调查详情 · 复盘报告                    │
│   (SSE 实时更新 + 审批面板 + 场景控制)                            │
└──────────────┬───────────────────────────────┬─────────────────┘
               │ HTTP + SSE (Vite Proxy 同源)    │ HTTP
┌──────────────▼───────────────────────────────▼─────────────────┐
│              Python AI 服务 · FastAPI (端口 8000)                │
│   Incident/AgentRun 管理 │ LangGraph Agent │ 受控工具层          │
│   审计落库 │ IncidentEvent 事件流 │ 演示场景代理                  │
└───────┬───────────────────────┬─────────────────┬──────────────┘
        │ 业务调用(带 traceId)    │ MySQL(只读/控制/执行) │ OpenAI-compatible
┌───────▼──────┐   ┌────────────▼──────────────┐   ┌─────────────┐
│ order-service│   │  MySQL 8                   │   │ LLM Provider│
│ inventory-svc│   │  tracemind_business        │   └─────────────┘
└──────────────┘   │  tracemind_control         │
                   │  performance_schema        │
                   │  information_schema        │
                   └───────────────────────────┘
```

组件职责:

- **Java 故障目标系统**:order-service(8081)、inventory-service(8082)。订单接口通过 HTTP 调用库存接口,请求携带 `traceId`(MDC + 响应头 `x-trace-id`),记录每个服务阶段耗时。暴露内部观测端点与演示场景控制端点。
- **MySQL 8**:`tracemind_business`(业务数据)、`tracemind_control`(Incident/审计/审批/报告)、`performance_schema`(慢查询证据)、`information_schema`(索引元数据)。
- **Python AI 服务**:唯一持有 LLM 与 Agent 逻辑的组件;通过受控工具访问 Java 服务和 MySQL;LangGraph Checkpoint 使用独立 SQLite 文件(AsyncSqliteSaver),业务审计写入 MySQL。
- **LLM**:兼容 Structured Output 与 Tool Calling 的 OpenAI-compatible 端点,经环境变量 `OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL` 配置(不同兼容服务对这两项能力的支持程度不一,选型时需确认)。

## 4. LangGraph 工作流

### 4.1 节点图

```
ingest
  → hypothesize
  → collect_evidence(单次:选工具 + 调一次 + 标准化 + 落 ToolCall/Evidence)
       ├─ 根因闸门未满足且预算充足 → collect_evidence
       ├─ 根因闸门满足 → diagnose
       └─ 预算耗尽 → report(needs_human)
  → diagnose(规则闸门)
       ├─ confirmed → propose_fix
       ├─ 未确认且预算充足 → collect_evidence
       └─ 预算耗尽 → report(needs_human)
  → propose_fix
  → human_approval [interrupt(), AsyncSqliteSaver + 固定 thread_id]
       ├─ rejected / expired → report
       └─ approved → execute_fix
  → execute_fix
       ├─ failed → report(action_failed)
       └─ succeeded → verify_recovery
  → verify_recovery(确定性规则判定)
       ├─ recovered → report(recovered)
       ├─ not_recovered → report(needs_human)
       └─ inconclusive → report(needs_human)
```

### 4.2 调查预算(防无限循环)

- `investigation_round` / `max_investigation_rounds`(默认 5)
- `tool_call_count` / `max_tool_calls`(默认 15,单轮内工具调用次数同样受全局上限约束)
- 任一预算耗尽且根因未确认 → `report(needs_human)`,不无限循环。
- 预算计数随状态持久化,恢复调查时继续累计。

### 4.3 根因判定规则(SCN-001,写死在 diagnose 节点)

当且仅当同时满足以下三条,才将"缺少联合索引 `idx_sku_warehouse(sku_id, warehouse_id)`"标记为 confirmed:

1. 慢查询证据存在(`list_expensive_query_digests` 增量显示目标查询扫描行数/耗时异常);
2. 执行计划证据(`get_query_plan` 的 `EXPLAIN FORMAT=JSON`)显示全表扫描或未使用目标联合索引;
3. 索引元数据证据(`get_index_info`)确认 `(sku_id, warehouse_id)` 联合索引确实缺失。

任一缺失 → 状态为 unknown,回到 collect_evidence 继续收集。

### 4.4 恢复判定规则(写死在 verify_recovery 节点,不让 LLM 决定)

全部满足才算 recovered:

- `index_present = true`(目标索引已存在);
- `query_plan_uses_target_index = true`(执行计划命中目标索引);
- `estimated_rows_after` 较 `estimated_rows_before` 明显下降;
- `latency_p95_after` 相对故障前基线恢复(不依赖绝对毫秒数,判定基于相对基线变化);
- `consecutive_healthy_checks >= 3`(连续 3 次采样通过)。

其余情况返回 `not_recovered` 或 `inconclusive`,均进入 `report(needs_human)`。V1.0 不自动回滚——修复动作是创建索引,未恢复时删除索引只会重新制造原始故障;只有当确认修复动作引入新风险时,才可提出新的回滚 Action 并再次走人工审批。

### 4.5 状态模型与合并规则

- LangGraph 顶层状态使用 `TypedDict`(图运行时无强校验需求);领域对象(`Hypothesis / Evidence / Diagnosis / FixProposal / ApprovalRecord / RecoveryCheck / Postmortem`)与 FastAPI 出入参使用 Pydantic v2 `BaseModel`(运行时校验)。
- `evidence`、`tool_calls` 为列表字段,必须配置 **reducer 按对象 ID 去重合并**(LangGraph 默认整字段覆盖,多个节点追加时会导致丢失)。

```python
class IncidentState(TypedDict):
    incident_id: str
    title: str
    description: str
    severity: str
    status: str                       # 见 4.6
    hypotheses: Annotated[list[Hypothesis], id_reducer]
    evidence: Annotated[list[Evidence], id_reducer]
    tool_calls: Annotated[list[ToolCallRecord], id_reducer]
    investigation_round: int
    max_investigation_rounds: int
    tool_call_count: int
    max_tool_calls: int
    termination_reason: str | None
    diagnosis: Diagnosis | None
    fix_proposal: FixProposal | None
    approval: ApprovalRecord | None
    recovery: RecoveryCheck | None
    report: Postmortem | None
```

### 4.6 status 枚举

`created / investigating / awaiting_approval / executing / verifying / recovered / needs_human / rejected / failed`

### 4.7 审批持久化(interrupt 的正确用法)

- 必须配合持久化 Checkpointer:V1.0 使用带持久卷的 **AsyncSqliteSaver**(LangGraph 官方本地工作流持久化方案;Checkpoint 存 SQLite,业务审计仍写 MySQL,不自研 Checkpointer)。
- 每次调查使用**稳定且唯一的 thread_id**(与 `agent_run` 绑定);审批恢复时使用原 thread_id。
- 审批节点在 `interrupt()` 前**不得执行不可重复的写操作**(LangGraph 恢复时会从包含 interrupt() 的节点开头重放,而非从断点行继续),节点内副作用需幂等。
- 审批绑定具体修复方案:`incident_id + fix_proposal_id + action_type + parameters_hash + approved_by + approved_at + expires_at`;修复方案变化后旧审批自动失效。

## 5. 受控工具层

### 5.1 工具清单与签名(定稿)

Agent 永远无法提交任意 SQL、Shell、服务名或表名;所有参数经 Pydantic 校验,枚举白名单由服务端维护。

| 工具 | 入参 | 出参核心字段 | 数据来源/账号 |
|---|---|---|---|
| `get_service_metrics` | `service_ref`, `window_seconds` | P95 / QPS / 错误率 | Java 内部观测接口(Micrometer)/ ai_investigator |
| `get_trace` | `trace_id` | 两服务阶段耗时组合;查无 → `TRACE_NOT_FOUND` | Java 观测记录 / ai_investigator |
| `list_expensive_query_digests` | `incident_id` | 增量:次数 / 总耗时 / 扫描行数 / `QUERY_SAMPLE_TEXT` | `performance_schema.events_statements_summary_by_digest` 基线差值 / ai_investigator |
| `get_query_plan` | `query_ref`, `sample_parameters` | `EXPLAIN FORMAT=JSON` 结果 | 查询白名单模板 / ai_investigator |
| `get_index_info` | `table_ref`(白名单) | 索引列表 | `information_schema.statistics` / ai_investigator |
| `execute_fix` | `incident_id`, `fix_proposal_id`, `approval_id` | 执行结果 | 预定义动作 / fix_executor 连接池(服务内部) |
| `verify_recovery` | `incident_id`, `fix_execution_id` | `index_present`、`query_plan_uses_target_index`、`estimated_rows_before/after`、`latency_p95_before/after`、`consecutive_healthy_checks`、`status` | 规则判定 / ai_investigator |

统一返回外壳:

```json
{ "tool_call_id": "", "success": true, "observed_at": "", "duration_ms": 0,
  "data": {}, "error_code": null, "error_message": null }
```

### 5.2 关键安全设计点

- **`get_query_plan` 不接收完整 SQL**:`query_ref` 必须来自服务端查询白名单(V1.0 仅 `INVENTORY_LOOKUP`),SQL 模板固化在代码中,参数经类型/范围/长度校验,服务端固定执行 `EXPLAIN FORMAT=JSON`。理由:MySQL `EXPLAIN` 支持 SELECT/DELETE/INSERT/UPDATE,不能依赖前缀拼接保证安全。
- **`list_expensive_query_digests` 使用基线差值**:digest 计数为累计值,Incident 创建时(ingest 节点)采集基线快照,调查时用当前值减基线,得到本次 Incident 期间的新增量。
- **`execute_fix` 执行前六项校验**:Incident 处于 `awaiting_approval`;Approval 状态为 approved;Approval 未过期且未消费;`parameters_hash` 与当前方案一致;`fix_proposal_id` 属于当前 Incident;幂等键 `incident_id + fix_proposal_id + parameters_hash` 无成功执行记录。实际 DDL 模板固化在代码中,`fix_definition` 表只存动作名称/风险级别/说明,不存可动态执行的 SQL。
- **`verify_recovery` 由规则计算**:LLM 不参与恢复与否的判断。

## 6. 数据模型

### 6.1 `tracemind_business`(业务库)

- `inventory`(库存表,目标表):含 `sku_id`、`warehouse_id`、`quantity` 等;目标联合索引 `idx_sku_warehouse(sku_id, warehouse_id)`;故障场景为该索引缺失。
- `orders`、`order_item` 等最小订单模型(仅支撑跨服务调用演示)。
- 数据量默认 50~100 万行,**通过环境变量配置**;初始化脚本负责灌量与建索引。

### 6.2 `tracemind_control`(控制库,13 张表)

| 表 | 关键字段 |
|---|---|
| `incident` | id, title, description, severity, status, created_at, finished_at |
| `agent_run` | id, incident_id, thread_id, status, investigation_round, tool_call_count, baseline_snapshot(JSON), started_at, finished_at |
| `hypothesis` | id, incident_id, description, status(proposed/supported/refuted/unknown), confidence, created_at |
| `evidence` | id, incident_id, tool_call_id, source, content, created_at |
| `hypothesis_evidence` | hypothesis_id, evidence_id, relation(supports/refutes) |
| `tool_call` | id, incident_id, tool_name, input(JSON), output(JSON), status, duration_ms, created_at |
| `fix_definition` | id, action_name, risk_level, description |
| `fix_proposal` | id, incident_id, fix_definition_id, parameters_json, parameters_hash, risk_level, reason, status, created_at |
| `approval` | id, incident_id, fix_proposal_id, action_type, parameters_hash, status, approver, comment, expires_at, consumed_at |
| `fix_execution` | id, incident_id, fix_proposal_id, approval_id, idempotency_key, status, result, created_at |
| `recovery_check` | id, incident_id, fix_execution_id, index_present, query_plan_uses_target_index, estimated_rows_before, estimated_rows_after, latency_p95_before, latency_p95_after, consecutive_healthy_checks, status, created_at |
| `postmortem` | id, incident_id, content(JSON), created_at |
| `incident_event` | id, incident_id, sequence, event_type, payload(JSON), occurred_at |

`incident_event` 为 SSE 事件持久化表,支持断线补发与审计。

### 6.3 数据库账号与连接池(四账号三连接池)

| 账号 | 权限 | 使用方 |
|---|---|---|
| `app_business` | `tracemind_business` 读写 | Java 业务服务 |
| `tracemind_control_app` | `tracemind_control` 必要 CRUD | Python 普通 Incident/审计代码 |
| `ai_investigator` | 只读业务表 + `performance_schema` + `information_schema` | Python 调查工具 |
| `fix_executor` | 目标表 INDEX 等受限权限 | Python execute_fix Action Adapter |

- MySQL 表级 INDEX 权限只能限制范围,不能保证只创建指定名称/字段的索引,因此**仍必须配合应用层白名单**。
- Python 服务内部使用三个独立连接池:`control_pool`(普通代码)、`readonly_pool`(调查工具)、`executor_pool`(仅 Action Adapter 持有)。

## 7. Java 故障目标系统

### 7.1 服务与调用链

- `order-service`(8081)、`inventory-service`(8082),独立 Spring Boot 3 应用,Java 21,MyBatis-Plus。
- 订单接口 HTTP 调用库存接口,请求带 `traceId`(MDC + 响应头 `x-trace-id`)。
- V1.0 定位为"真实跨服务调用 + traceId 关联 + 各服务阶段耗时记录",**不宣称完整分布式追踪**;OpenTelemetry 意义上的 Trace/Span 留待 V1.2。

### 7.2 观测与指标

- 使用 Spring Boot Actuator 集成 **Micrometer**:Timer 记录接口耗时、配置 P95 直方图、Counter 计算 QPS。
- 通过内部观测接口返回**固定结构**(P95/QPS/错误率),不把 Actuator 原始响应直接暴露给 Agent。
- 两个服务分别维护有容量与过期限制的观测记录:
  - `order-service`:`total_duration_ms`、`inventory_http_duration_ms`
  - `inventory-service`:`total_duration_ms`、`database_duration_ms`
- 观测记录使用内存 TTL 缓存或固定长度 Ring Buffer(如保留最近 10 分钟、最多 10,000 条);服务重启后记录丢失在 V1.0 可接受,但必须返回 `TRACE_NOT_FOUND`,**不允许 LLM 补造缺失数据**。
- 提供独立演示负载发生器(脚本或 Compose 可选 profile,不进正式业务接口),保证演示期间有持续请求、指标非空。

### 7.3 演示场景控制(与处置路径隔离)

```
POST /internal/scenarios/SCN-001/inject    # 注入故障:drop 目标联合索引
POST /internal/scenarios/SCN-001/reset     # 实验环境重置:重建索引等
GET  /internal/scenarios/SCN-001/status    # 场景状态
```

- 仅在 `DEMO_MODE=true` 时启用;使用独立管理密钥,密钥仅存于 AI 服务环境变量,不出现在 Vue 代码中。
- `inject` 用于开始演示;`reset` 只用于演示前后的环境初始化,**属于"实验环境重置"而非正式故障处置**,运行中的 Incident 禁止调用 reset。
- 正常处置过程中,创建索引只能通过审批后的 `execute_fix`;`execute_fix` 是 Incident 处置过程中的唯一写路径。
- 注入与重置同样记录审计日志;`inject`/`reset` 均幂等。

### 7.4 查询与索引定义(全文统一,不得出现第二种写法)

- 目标查询:`SELECT ... FROM inventory WHERE sku_id = ? AND warehouse_id = ?`
- 目标联合索引:`idx_sku_warehouse(sku_id, warehouse_id)`
- 上述定义在查询白名单、目标索引、诊断规则、恢复验证中保持一致。

## 8. AI 服务 API

创建事件与启动调查分离,便于失败重试、回放与状态管理:

```
POST /api/incidents                                      # 创建 Incident + 采集基线 → 201
GET  /api/incidents                                      # 列表(可按状态筛选)
GET  /api/incidents/{incident_id}                        # 详情快照
POST /api/incidents/{incident_id}/investigations         # 创建 AgentRun + 异步启动 → 202(重复请求不重复启动)
GET  /api/incidents/{incident_id}/runs/{run_id}          # 单次调查详情
GET  /api/incidents/{incident_id}/stream                 # SSE(Last-Event-ID 支持)
POST /api/incidents/{incident_id}/approvals/{approval_id}/decision   # 审批决策
GET  /api/incidents/{incident_id}/report                 # 复盘报告
POST /api/demo/scenarios/SCN-001/{inject|reset|status}   # 演示场景代理(仅 DEMO_MODE)
```

审批决策请求体:`{ "decision": "approved" | "rejected", "comment": "..." }`。
服务端校验:Approval 属于当前 Incident;FixProposal 未变化;`parameters_hash` 一致;Approval 未过期未消费;Incident 处于 `awaiting_approval`;相同 Action 未成功执行过。**审批人身份由服务端确定,不信任请求体中的 `approved_by`**。

## 9. SSE 设计

事件类型:`incident_status / tool_call / evidence / hypothesis_update / diagnosis / approval_request / fix_execution / recovery / report`。

统一结构:

```json
{ "event_id": 125, "incident_id": "inc_xxx", "sequence": 18,
  "occurred_at": "2026-08-10T10:30:00Z", "payload": {} }
```

要求:

- 持久化到 `incident_event`;SSE 按 `Last-Event-ID` 断线补发;前端按 `event_id` 去重。
- 15~30 秒发送一次 heartbeat。
- Incident 进入终态后发送最终事件并关闭连接。
- 页面首次打开先请求 Incident 当前快照,再连接 SSE;浏览器重连后只补发缺失事件。
- 开发阶段 Vue 使用 Vite Proxy 将 `/api` 转发到 FastAPI,EventSource 使用同源地址,避免跨域与自定义认证 Header 问题。

## 10. Vue 工作台(V1.0 三个页面)

1. **场景与事件列表**:当前场景状态、注入故障、重置环境、创建 Incident、Incident 列表与状态筛选。
2. **Incident 调查详情**:Incident 基本信息、Agent 当前状态、调查轮次与工具预算、假设卡片、证据链、工具调用记录、根因判断、修复方案、审批面板、修复执行结果、恢复验证结果。
3. **复盘报告**:故障摘要、调查时间线、根因与证据、审批信息、修复动作、恢复结论、未解决问题。

技术栈:Vue 3 + TypeScript + Vite + Element Plus + EventSource(SSE)。不做复杂用户管理、通用聊天窗口、可拖拽工作流、监控大屏。

## 11. 安全设计(汇总)

- 四数据库账号隔离 + Python 三连接池;`execute_fix` 为唯一写路径。
- 工具白名单;Agent 不能提交任意 SQL、Shell、服务名、表名、完整查询。
- `get_query_plan` 白名单模板;`execute_fix` 六项审批校验 + 幂等键。
- 场景管理路径与 Incident 处置路径隔离;`reset` 仅演示环境,运行中 Incident 禁止调用。
- 根因与恢复结论由规则确定,LLM 不直接判定。
- 工具返回不存在时明确报错(如 `TRACE_NOT_FOUND`),防 LLM 幻觉补造。
- 所有工具参数经 Pydantic 校验;服务名/表名/查询使用服务端枚举。
- LLM 输出与工具返回均视为不可信数据。
- 日志、SSE、Evidence 不得包含密码及 API Key。
- Approval 使用数据库原子更新,防止并发重复审批;审批绑定具体 FixProposal,方案变化后旧审批失效。
- 工具超时、重试与调用次数受限。
- 审批人信息由服务端身份确定。
- V1.0 为本地演示环境,不宣称具备生产级身份认证。

## 12. 测试策略

### Java(前期)

- JUnit 5 + Mockito + 本地 MySQL 测试库。
- 覆盖:traceId 生成与传递、阶段耗时记录、指标端点、场景注入幂等、场景重置幂等、健康状态存在目标索引、故障状态目标索引缺失。
- Testcontainers 推迟到 M5。

### Python

- pytest + httpx + FakeLLM + Mock Tool + 临时 SQLite Checkpointer。
- 覆盖:完整 Agent 图、审批中断与恢复、预算耗尽进入 needs_human、证据不完整不能确认根因、审批过期/哈希不匹配不能执行、`execute_fix` 幂等、恢复规则判定、SSE 事件顺序与断线补发、API 集成流程。

### 前端(Vitest)

- SSE 事件合并与去重、Incident 状态更新、审批按钮显示条件、重复审批保护、`needs_human` 与异常状态展示。

### 端到端(M5)

完整闭环:重置场景 → 注入故障 → 产生负载 → 创建 Incident → 启动调查 → 确认根因 → 人工审批 → 创建索引 → 验证恢复 → 生成报告。

性能判定不只看绝对毫秒数,同时验证:索引是否存在、EXPLAIN 是否使用目标索引、扫描行数是否明显下降、P95 是否相对基线恢复。

## 13. 开发与部署方式

### 13.1 日常开发(不使用 Docker Compose,本地进程)

| 组件 | 地址 |
|---|---|
| Windows 本机 MySQL 8 | localhost:3306 |
| order-service | localhost:8081 |
| inventory-service | localhost:8082 |
| FastAPI AI Service | localhost:8000 |
| Vue Vite Dev Server | localhost:5173 |
| LangGraph SQLite Checkpoint | 本地文件 |

分别通过 IDEA、Python 终端、Vite 启动,便于断点调试。

### 13.2 配置与脚本

- `.env.example`(提交)、`.env.local`(真实密码,加入 `.gitignore`)。
- 脚本:`scripts/init-database.ps1`、`scripts/generate-data.ps1`、`scripts/start-dev.ps1`、`scripts/run-load.ps1`。
- 配置项(全部走环境变量,不写死 localhost):`BUSINESS_DB_URL`、`CONTROL_DB_URL`、`INVENTORY_SERVICE_URL`、`ORDER_SERVICE_URL`、`AI_SERVICE_URL`、`OPENAI_BASE_URL`、`OPENAI_API_KEY`、`OPENAI_MODEL`、`CHECKPOINT_DB_PATH`、`DEMO_MODE`。

### 13.3 最终交付(M5)

Docker Compose 仅用于一键演示、面试快速运行、CI 集成验证与项目最终交付,包含:`mysql`、`order-service`、`inventory-service`、`ai-service`、`web`,以及可选 `loadgen` profile。

## 14. 里程碑

| 里程碑 | 内容 | 验收标准 |
|---|---|---|
| **M1 Java 故障目标** | 本地 MySQL 双 Schema、四账号权限、Java 双服务、traceId+阶段耗时、Micrometer 指标、数据生成、SCN-001 注入/重置 | 可稳定看到有索引与缺索引时执行计划差异 |
| **M2 AI 服务与工具层** | FastAPI 骨架、控制库 13 表、Incident 基线、七受控工具、四账号三连接池、审计与 IncidentEvent | 不调用 LLM 也能手动取得完整诊断证据 |
| **M3 LangGraph 闭环** | 九节点 Agent、FakeLLM 与真实模型适配、调查预算、确定性根因/恢复判定、AsyncSqliteSaver、审批中断恢复、幂等修复、复盘报告 | 通过 FastAPI 接口即可完成完整闭环 |
| **M4 Vue 工作台** | 事件列表、调查详情、审批面板、SSE 实时更新、场景控制、复盘报告 | 不使用命令行即可演示完整流程 |
| **M5 最终交付** | Testcontainers、前后端自动化测试、Dockerfile、Docker Compose、E2E、README/架构图/演示脚本 | 全新环境通过 Docker Compose 一次启动并重复演示 |

V1.0 完整验收后进入 **V1.1**:Qdrant、Runbook RAG、Agent 评测集、调查回放、回归评测。

## 15. 演示脚本(面试用)

1. `reset` 重置实验环境(重建索引、清数据)。
2. `inject` 注入故障(drop 联合索引)。
3. 启动负载发生器产生持续请求。
4. 创建 Incident → 启动调查。
5. 观察 SSE:假设卡、证据链、工具调用逐条出现。
6. 根因确认:缺少联合索引。
7. 审批面板:展示风险与回滚说明,人工批准。
8. `execute_fix` 创建索引 → 恢复验证通过。
9. 查看复盘报告(时间线 + 证据 + 结论)。
