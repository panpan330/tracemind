# TraceMind 版本演进

> 各版本详细技术说明。README 首页只保留精炼版本史。

---
## V1.1:真实 LLM + Tool Calling + RAG + 评测体系

### 三模式 LLM

| 模式 | 行为 | 用途 |
|---|---|---|
| `TRACEMIND_LLM_MODE=fake` | FakeLLM(确定性,不触网) | 测试 / 显式回归 |
| `real_strict` | 模型失败即转 needs_human,禁止降级 | 正式评测 / 验收 |
| `real_demo` | 模型失败降级到确定性组件并标记 `degraded` | 演示兜底 |

真实模型走 OpenAI 兼容端点(百炼),`hypothesize` 用结构化输出(容忍 markdown fence),
工具选择走 **真实 Tool Calling**(`collect_evidence` 混合循环:LLM 选工具 → 程序校验/白名单/去重/预算),
`propose_fix` 完全确定性(`FixRegistry`,零 LLM 调用,参数 hash 固定)。

### RAG 知识库

- 10 篇 Runbook(`knowledge/runbooks/`,frontmatter 元数据)经 embedding 写入 Qdrant(Collection Alias + 读写 Key 分离)。
- 入库:`cd ai-service && uv run python ../scripts/seed_runbook.py [--recreate]`(幂等)。
- `hypothesize` 检索 top-k 片段注入 prompt(指令隔离:知识参考不可被当作指令执行),并写 `retrieval_record` 审计。
- 失败降级:检索失败 → `rag_degraded` 事件 → 无知识上下文继续,`TRACEMIND_RAG_MODE=required` 时该次验收判失败。

### 真实模型验收(显式切换)

```bash
# compose 环境:TRACEMIND_LLM_MODE=real_strict + TRACEMIND_RAG_MODE=required + 启动 qdrant 服务
TRACEMIND_LLM_MODE=real_strict TRACEMIND_EVAL_MODE=true \
  uv run python ../scripts/eval_agent.py --mode offline --llm real_strict --runs 3   # 真实模型离线评测
uv run python ../scripts/smoke_llm.py               # 真实模型冒烟(Structured Output + Tool Calling,禁止假通过)
# e2e-scn001-real:全栈真实模型 3 轮闭环(需百炼额度,见 tracemind-real-model-quota)
```

## V1.2:MCP 工具服务(stdio)

将只读调查工具封装为基于官方 SDK 的 **stdio MCP Server**(V1.3 后为 7 个)。LangGraph Agent 通过持久化 MCP Client 会话完成工具发现和调用,**消除调查工具进程内 direct 路径**;`execute_fix` / `verify_recovery` 属确定性安全控制节点,不纳入 MCP。

- **生命周期**:FastAPI lifespan 启动时 spawn MCP Server → initialize → 契约校验(serverInfo / 工具集合 / inputSchema 签名)→ readiness;关闭时有序终止;启动失败即应用启动失败
- **上下文注入**:`incident_id` / `agent_run_id` 由 MCP Client 注入,LLM 侧 Schema 隐藏,仅审计
- **审计**:`tool_call` 表新增 `agent_run_id` / `transport`(`mcp_stdio` / `internal_control` / `legacy_direct`)/ `mcp_invocation_id` / `mcp_attempt`(版本化迁移 `scripts/sql/05-v12-mcp-migration.sql`)
- **错误码**:`MCP_START_FAILED / MCP_SCHEMA_MISMATCH / MCP_TIMEOUT / MCP_DISCONNECTED / MCP_PROTOCOL_ERROR / MCP_TOOL_ERROR / MCP_RESULT_INVALID`;业务失败保留原始 error_code;重启最多一次,不降级 direct
- **测试**:协议集成(真实 stdio + stdout 纯净)、故障注入(主动终止不 direct fallback)、全栈回归断言 `transport=mcp_stdio`

## V1.3:多故障场景(SCN-002 锁等待)+ 回归评测流水线

### 场景与双 Policy

| 场景 | 故障注入 | 证据链 | 根因 | 处置动作 |
|---|---|---|---|---|
| SCN-001 | 删除联合索引 | E1~E5 | `MISSING_INVENTORY_INDEX` | `CREATE_INVENTORY_INDEX`(审批后建索引) |
| SCN-002 | 后台长事务 `FOR UPDATE` 持锁 42/7 | L1~L6 | `LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION` | `TERMINATE_BLOCKING_SESSION`(KILL 阻塞会话) |

- **共享 Fact 层**(8 个布尔)派生双 Policy 状态;诊断按四分支判定:单根因确认 / 多根因冲突 / 全部否定 / 证据不足;`X-NO-TARGET-LOCK-WAIT` 为可排除项(已采且无锁等待即排除锁根因)。
- **锁调查工具**:`get_lock_waiters`(performance_schema `data_lock_waits` + `data_locks` + `threads` 真实查询)与 `get_transaction_details`(`innodb_trx`),`blocker_ref = blk_<processlist_id>` 桥接两个 ID 空间。
- **处置安全(session_terminator)**:第五账号(KILL 前复核:审批有效未过期、`blocking_transaction_id` 一致、仍持锁、账号白名单、非系统线程),幂等防重复处置;`blocking_relation_hash`(10 项稳定身份,不含时间)固化审批与执行目标。
- **观测语义**:`get_service_metrics` 空窗口(P95 null)视为"未采集"允许重采;锁等待期间查询耗时集中数据库阶段(即使超时也记录),保证证据链在真实故障下成立。
- **恢复验证**:锁场景按目标范围(索引存在 / 计划走索引 / 无锁等待 / 阻塞会话已终止 / P95 相对基线)轮询验证,超时 `recovery_timeout` 转人工。

### 回归评测流水线

```bash
python scripts/run_regression.py --tier fast    # fast 档:单测 + 离线评测 + 处置安全(分钟级,不依赖外部服务)
python scripts/run_regression.py --tier full    # full 档:fast + SCN-001/SCN-002 全链路 E2E(需全栈)
```

- 报告写入 `reports/regression/`,记录 Git SHA / 版本 / 各阶段耗时 / 失败原因,失败时非零退出码。
- 24 条离线评测 Fixture(16 索引 + 8 锁,动态 N/N)+ 处置安全套件(合法 KILL 恰一次 / 未审批禁 KILL / 负例零误杀)。
- 关键回归项:`p95 null 不产 E1 证据且允许重采`、`已采集证据不重采`、`双 Policy 四分支`、`transport 全 mcp_stdio 无 direct`。

### 配置表(V1.3 新增)

`TRACEMIND_SESSION_TERMINATOR_DB_URL`(会话终止专用账号,无 database)· `TRACEMIND_RECOVERY_TIMEOUT`(恢复轮询超时,默认 60s)

## V1.4:真实可观测性(Prometheus + Jaeger + OTel)

### 观测架构

```
Java(OTel Agent 2.12.0,管理端口 9081/9082)
  ├─ Trace: OTLP/gRPC → otel-collector(trace-only) → jaeger(内存存储)
  └─ Metrics: Micrometer Prometheus 端点 → prometheus → grafana
ai-service(TRACEMIND_METRICS_BACKEND / TRACE_BACKEND 切换)
  ├─ get_service_metrics → PrometheusMetricsClient(固定 PromQL 模板注册表,新鲜度判定)
  └─ get_trace → JaegerTraceClient(HTTP JSON API)+ TraceNormalizer(TRACE_NORMALIZER_V1)
```

- **证据语义**:E1 证据含 `sourceBackend/observationQueryId/windowStart/windowEnd/latestSampleAt`;E2 证据含 `dbDominanceRatio`(数据库阶段占比 ≥0.5)+ `traceId`(Jaeger 可再查)。get_trace 由**异常时间窗口**驱动(planner 经 `trace_ref=REPRESENTATIVE_SLOW_TRACE` 抽象引用解析)。
- **观测审计**:`observation_query` 表记录每次查询(backend/template_id/状态/耗时/结果哈希/归一化结果),不存原始 Prometheus/Jaeger 响应。
- **不回退 internal**:`full` 回归档强制 `metrics_backend=prometheus + trace_backend=jaeger`;Prometheus/Jaeger 停止时工具返回 `METRICS_BACKEND_UNAVAILABLE` / `TRACE_BACKEND_UNAVAILABLE`,调查明确失败,绝不回落旧内部观测。
- **compose 网络分区**:`tracemind`(业务)/ `metrics-scrape-net`(Java→Prometheus)/ `trace-ingest-net`(Java→Collector→Jaeger)/ `observability-query-net`(AI/Grafana→Prometheus/Jaeger);Grafana/Jaeger UI 仅宿主机回环 + `observability-ui` profile。

### 验证命令(V1.4)

```bash
# 本地 fixture 冒烟(不需要 Prometheus/Jaeger)
python scripts/verify-m14.py --base http://localhost:8000 --order http://localhost:8081 --fixture --rounds 1
# VM 全量验收(全栈 + 观测栈;要求 metrics_backend=prometheus + trace_backend=jaeger)
python scripts/verify-m14.py --base http://<vm-host>:8000 --order http://<vm-host>:8081
python scripts/verify-observability-resilience.py --base http://<vm-host>:8000
python scripts/verify-grafana-smoke.py --grafana http://127.0.0.1:3000
python scripts/run_regression.py --tier full   # full 档强制真实后端
```

## V1.5:证据与决策链回放(Replay)

### 回放架构

```
调查时写入(不可变快照)                   读取时投影(只读,零副作用)
Agent 节点执行 → ReplayWriter           Replay API(按 Run 限定)
  └─ incident_replay_step(纯追加)  →     Replay Projector → Manifest/steps
       logical_step_id × attempt_no        stepIndex/displayDurationMs/keyStepIndexes
       phase: started → completed/failed   runOutcome/terminationReason
前端 ReplayView(本地播放,不重算 Policy)
  └─ position 语义(状态位置)/ 单次 setTimeout / 状态机 / 控制条
```

- **不可变证据**:`incident_replay_step` 纯追加、禁 UPDATE/DELETE;`sequence_no` 原子分配与插入同事务;每步含 `snapshotHash`(Canonical JSON SHA-256,一致性校验,非防篡改)与 `source_reference`(引用不可变版本 + 冻结摘要,不塞原始响应)。
- **两段式 phase**:同 `logical_step_id` 先 `started` 后 `completed|failed`;按 `(logical_step_id, attempt_no)` 组装 Attempt(重试/审批拒绝→新 Attempt);`RUN_TERMINATED` 记录 `runOutcome`(recovered/failed/rejected/needs_human)与 `terminationReason`。
- **只读 API**:`GET /api/incidents/{id}/replay`、`/replay/runs/{run_id}`、`/steps`、`/steps/{logical_step_id}`;不触发状态机、不调 LLM/MCP、不执行审批处置、不重算 Policy;`asOfSequenceNo` 保证 Manifest 与 steps 同一截面;runId 归属校验 404。
- **版本冻结与校验**:Run 启动时冻结 `expected_*` 版本常量,Step 记录实际使用版本;恢复 Run 前校验不一致 → `version_mismatch`,停止原 Run 不按当前程序补算。
- **前端回放页**:`web/src/views/ReplayView.vue` + `useReplayPlayback`(position 状态语义、状态机、倍速、跳转暂停);`partial` 时展示缺失部分,绝不按当前 Policy 补算;只读横幅提示"不会执行任何系统操作"。

### 验证命令(V1.5)

```bash
# 本地 fixture 验收(需本地 MySQL + 三个服务已启动)
python scripts/verify-m15.py --base http://localhost:8000 --order http://localhost:8081
# VM 全栈验收(代码同步 + 镜像重建后执行;--order 的 host 自动作为 pymysql 直连地址)
python scripts/verify-m15.py --base http://<vm-host>:8000 --order http://<vm-host>:8081
```

- 断言内容:SCN-002 完整闭环 → `replayStatus=complete` + 11 类必需步骤 + `runOutcome=recovered`;rejected 路径 → `runOutcome=rejected` 且不要求 `FIX_EXECUTED`;只读无副作用(重复读取一致 / runId 归属 404)。
- **部署注意**:compose 部署下 ai-service 必须配 `TRACEMIND_SESSION_TERMINATOR_DB_URL`(缺省时处置 KILL 回退只读账号报 500),本地直接跑 python 用 `.env.local` 不受影响。


## V1.6:正式迁移器 + Run Profile + 评测缺陷修复

### 回归测试方法(回归 V1.4/V1.5 手动验证)

- **正式迁移器**:`scripts/db/migrate.py`(唯一入口,checksum/幂等/Advisory Lock/账号 Provisioning),迁移文件 `scripts/db/migrations/`;本地与 VM 部署共用。
- **Run Profile**:`TRACEMIND_RUN_PROFILE = local|ci_db|offline_eval|full_e2e|production`(fail-closed:严格档缺 URL / LLM 模式不匹配即启动失败;offline_eval 禁数据库访问)。
- **离线评测缺陷修复**:fixture 的 `metrics.representativeSlowTraceId` 必须配套 `get_trace` 条目(缺则 POS 全 FAIL);新增回归测试 `test_fixture_trace_id_contract`,当前 24/24 PASS。
- **真实模型验收 + agent 稳定性修复**(VM 全量 PASS,SCN-001/002 各 3 轮):`compute_eligible_tools` 的 `satisfied` 改为"证据已采集即满足"(passed=False 的确定性否定不再让工具永远 eligible,消除 duplicate 死循环);`select_tool` 在真实 LLM 连续失败时确定性 planner 兜底(消除 llm_unavailable);eligible 唯一时确定性执行。

### 验证命令

```bash
# 后端单测(需本地 MySQL)
cd ai-service && .venv/Scripts/pytest.exe tests/ -q
# 离线评测(24 case,fake LLM,无网络)
cd ai-service && TRACEMIND_RUN_PROFILE=offline_eval TRACEMIND_LLM_MODE=fake TRACEMIND_EVAL_MODE=true \
  .venv/Scripts/python.exe ../scripts/eval_agent.py --mode offline --llm fake --runs 1
# 覆盖率:ai-service 测试 238 passed + pytest-cov(可选)
cd ai-service && .venv/Scripts/pytest.exe tests/ -q
# 迁移器(建库 + 版本化迁移)
python scripts/db/migrate.py --init-db --migrations scripts/db/migrations
# VM 发布验收(V1.4/V1.5 同款,需 VM 部署)
python scripts/verify-m14.py --base http://<vm-host>:8000 --order http://<vm-host>:8081
```

## V1.7:MCP Streamable HTTP 远程传输与服务化

MCP 工具服务从"AI 服务内部 spawn 的 stdio 子进程"升级为**独立容器、独立镜像、Streamable HTTP 标准传输**的远程只读工具服务(工具实现唯一,stdio 与 HTTP 只是两个 Transport Adapter,零 direct bypass)。

### 传输定位

| 环境 | 传输 |
|---|---|
| 本地开发 / 离线评测 | stdio(Fixture Server) |
| **VM 标准部署(演示/验收)** | **Streamable HTTP** |

- 标准部署只走 Streamable HTTP,网络错误**不降级** stdio/direct(`direct_fallback=false`)。
- `execute_fix` / `verify_recovery` 不暴露为远程 MCP Tool(仍是 AI 服务内部确定性安全节点)。
- 无状态(`stateless_http=True`),支持独立部署与水平扩展基础。

### 关键配置

```bash
# ai-service(标准部署)
TRACEMIND_MCP_TRANSPORT=streamable_http
TRACEMIND_MCP_HTTP_URL=http://mcp-tools:8001/mcp
TRACEMIND_MCP_HTTP_BEARER_TOKEN=<部署时生成>

# mcp-tools(独立容器,见 compose.yml)
TRACEMIND_MCP_AUTH_CLIENTS_FILE=/run/secrets/mcp_clients.json   # Token Fingerprint → Principal+Scopes
TRACEMIND_MCP_AUDIT_DB_URL=mysql+pymysql://mcp_tool_auditor:...@mysql:3306/tracemind_control
```

- 认证:内部 Opaque Token(不做 JWT/OAuth);`client_id` 只从认证结果派生;Token 不进 LLM Prompt/Schema/审计/日志。
- 审计唯一所有者:AI 服务写 `tool_call`,MCP Server 写 `tool_call_attempt`(两段式,`mcp_tool_auditor` 最小权限)。
- 版本四维度:Tool Schema / MCP Protocol / SDK Version(读实际安装包)/ InvocationContext。

### 验证命令(V1.7)

```bash
# 统一三层验收入口(Python 编排,人工触发自动执行)
python scripts/verify-m17.py --tier fast        # 本地快速回归(后端全量 + Vue typecheck + 离线评测 N/N)
python scripts/verify-m17.py --tier vm-smoke    # VM 标准部署(独立容器 + HTTP 探针 + SCN 闭环 + 凭据隔离)
python scripts/verify-m17.py --tier release     # 真实模型发布验收(real_strict,发版前)
```

**Release 前绑定断言**(打 V1.7 Tag 前):工作树干净;报告 Git SHA == 当前 HEAD;镜像 label Git SHA == HEAD;Digest 已写入报告;Tag 指向已验收 Commit。

**设计文档**:`docs/superpowers/specs/2026-08-13-tracemind-v17-mcp-streamable-http-design.md`

## V1.8:Agent 运行观测面板 + 量化评测报告

Agent 运行观测面板 + 量化评测报告(成功率/耗时/tokens)。

## V1.9:长期记忆 + 上下文压缩

qdrant 案例沉淀 + 语义检索复用 + EvidenceSummarizer 压缩。

## V1.10:反思自改进 + 负样本记忆

reflect 结构化复盘 + 3 轮重试 + 失败案例避坑检索。

## V1.11:多模型路由 + 成本统计 + 容灾

节点级选模型 + 成本账单(单次 ¥0.0153)+ fallback 容灾。

## V1.12:动态路由学习

窗口滚动评分(成功率/时延/成本加权)+ ε-greedy 探索。

## V1.13:评测平台可视化

eval_run 持久化 + 前端列表/详情/趋势。

## V1.14:Agent 进度面板

SSE 事件前端实时展示,节点级渐进可视化。
