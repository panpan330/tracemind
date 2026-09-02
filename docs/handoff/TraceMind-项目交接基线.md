# TraceMind 项目交接基线

> 本文档为**没有历史对话背景的新编码 Agent** 提供 TraceMind 项目的当前事实基线。
> **事实来源 = 当前仓库代码**;历史对话仅用于解释设计动机,不构成当前能力声明。
> 状态标记:**已实现 / 部分实现 / 未实现 / 未验证 / 待核查**。

---

## 1. 文档快照信息

| 项 | 值 |
|---|---|
| 生成时间 | 2026-08-14(以实际提交时间为准);**V2.0-A 更新:2026-09-02** |
| Git SHA | `92e2a5d3b9f95c515422724a390d3075f78cf54e` → **V2.0-A 基线可信化后见 git log** |
| 分支 | `main` |
| git status | 干净(无未提交改动) |
| 适用代码版本 | 上述 SHA;仓库若变化,新 Agent 必须重新核对本文档与代码差异 |

---

## 2. 项目定位与当前能力

### 2.1 解决的问题

微服务系统出现性能故障时,人工排查慢查询/锁等待耗时且易漏。项目让 AI Agent 基于**真实证据**(MySQL 执行计划、慢查询、锁等待关系、P95、Trace)自动完成"根因定位 → 修复方案 → 人工审批 → 受控执行 → 恢复验证 → 复盘报告"闭环,全程可审计、可回放。

### 2.2 当前真正支持的故障场景(已实现)

| 场景 | 根因 | 处置动作 |
|---|---|---|
| SCN-001 | `MISSING_INVENTORY_INDEX`(缺失 `idx_sku_warehouse` 联合索引) | `CREATE_INVENTORY_INDEX`(审批后建索引) |
| SCN-002 | `LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION`(长事务持锁) | `TERMINATE_BLOCKING_SESSION`(审批后 KILL 阻塞会话) |

**仅这两类。** 未实现缓存失效、连接池耗尽、网络超时、JVM 故障等其他场景。

### 2.3 已实现能力(已实现)

- **证据驱动诊断闭环**:ingest → hypothesize → collect_evidence(循环)→ diagnose → propose_fix → human_approval(interrupt)→ execute_fix → verify_recovery → report。
- **反思重试(V1.10)**:verify_recovery 未恢复 → reflect 节点结构化复盘,最多 3 轮重试,用尽转 needs_human。
- **人工审批(human-in-the-loop)**:唯一写路径经 `interrupt()` 挂起,过期自动拒绝。
- **受控执行**:execute_fix 确定性节点(预定义 DDL/KILL + 六项校验 + 幂等)。
- **恢复验证**:verify_recovery 三批固定探测,相对健康基线判定。
- **回放(Replay)**:`incident_replay_step` 纯追加不可变快照,只读 Replay API + 前端回放页。
- **SSE 实时事件流**:`incident_event` 持久化 + Last-Event-ID 断线补发。
- **长期记忆(V1.9)**:qdrant 案例沉淀(`tracemind_case_memory`)+ hypothesize 语义检索复用;失败案例 `recovered=false` 负样本避坑。
- **多模型路由(V1.11/1.12)**:节点级模型路由 + 动态窗口评分(默认关)+ ε-greedy(默认关)+ 成本统计。
- **评测平台(V1.13)**:`eval_run` 表 + `GET /api/evals` 列表/详情 + 前端评测页 + 触发 API。
- **Agent 进度面板(V1.14)**:前端消费 SSE 事件展示节点级进度。

### 2.4 明确未实现(未实现)

- 多 Agent 协作(Planner/Worker/Reviewer)——README 路线图列为下一步。
- LLM token 级流式输出(当前为节点级事件)。
- 成本预算告警/失败案例自动淘汰/评测自动触发仅配置开关,默认关闭。

---

## 3. 系统架构

### 3.1 组件与端口

| 组件 | 技术 | 端口 | 职责 |
|---|---|---|---|
| web | Vue 3 + TS + Vite + Element Plus | 8080(nginx) | 工作台:场景控制/详情(SSE)/审批/回放/评测/进度面板 |
| ai-service | Python 3.12 / FastAPI / LangGraph / SQLAlchemy | 8000 | Incident 管理、Agent 状态机、审计、SSE、评测 API |
| mcp-tools | Python MCP(Streamable HTTP) | 8001 | 7 个只读调查工具(独立容器) |
| order-service | Java 21 / Spring Boot 3.3 | 8081 | 订单服务,调用 inventory,携带 traceId |
| inventory-service | Java 21 / Spring Boot 3.3 | 8082 | 库存查询(故障载体),SCN-001/002 注入/重置 |
| mysql | MySQL 8.0.39 | 3306 | business(真实数据)+ control(审计/事件)+ performance_schema |
| qdrant | qdrant/qdrant v1.18.2 | 6333 | 长期记忆向量存储 |
| prometheus / jaeger / otel-collector / grafana | 观测栈 | 9090 / 16686 / 4317 / 3000 | 真实 P95 / Trace 数据源(observability-ui profile) |

### 3.2 服务调用关系

```
web → ai-service(/api,SSE)
ai-service → mcp-tools(Streamable HTTP:工具调用)→ MySQL/观测栈
ai-service → LLM Provider(qwen 系列,fake/real_strict/real_demo)
mcp-tools → order-service/inventory-service(观测接口)
mcp-tools → MySQL(只读账号 performance_schema/information_schema)
ai-service → MySQL control 库(审计/事件)
```

### 3.3 启动顺序(compose)

1. mysql(initdb 自动)→ seed(幂等灌 50 万行)
2. order-service / inventory-service
3. mcp-tools → ai-service → web
4. 观测栈(otel-collector → jaeger/prometheus → grafana,`--profile observability-ui up -d`)

> **注意(V1.10 踩坑)**:观测栈挂 `profiles: [observability-ui]`,`docker compose up -d` 默认不启动;mcp-tools 硬编码 `METRICS_BACKEND=prometheus`,观测栈未启动时 `get_service_metrics` 返回 `METRICS_BACKEND_UNAVAILABLE`。

### 3.4 入口文件

| 层 | 入口 |
|---|---|
| AI 服务 | `ai-service/app/main.py`(FastAPI + lifespan) |
| LangGraph 图 | `ai-service/app/agent/graph.py`(build_graph) |
| 节点实现 | `ai-service/app/agent/nodes.py` |
| 状态 | `ai-service/app/agent/state.py`(IncidentState) |
| 执行管理 | `ai-service/app/services/runner.py`(checkpointer/start/resume/recover) |
| Java 场景控制 | `java/inventory-service/.../scenario/ScenarioController.java` + `ScenarioService.java` |

---

## 4. 核心业务流程

### 4.1 Incident 创建

- 入口:`ai-service/app/api/incidents.py`。
- 模型:`Incident`(`app/db/models.py:17`),`status` 字段(created/investigating/awaiting_approval/recovered/needs_human/rejected/failed)。

### 4.2 AgentRun 创建

- 入口:`ai-service/app/api/runs.py:25` → `runner.start_investigation`。
- 模型:`AgentRun`(`app/db/models.py:39`),`thread_id` 唯一(`run-{uuid4}`,`run_repo.py:12`)。
- checkpoint 路径:`settings.checkpoint_path`(默认 `./data/checkpoints.sqlite`)。

### 4.3 LangGraph 调查与证据采集

- 图:`graph.py` 10 节点(ingest/hypothesize/collect_evidence/diagnose/propose_fix/human_approval/execute_fix/verify_recovery/reflect/report)+ 条件边(diagnose/approval/verify_recovery/reflect)。
- 证据采集:`collect_evidence`(`nodes.py`),工具选择走 `llm.select_tool`(真实 Tool Calling)或确定性 planner 兜底(`tool_calling.py`)。
- 预算:MAX_DECISION_ATTEMPTS=14、MAX_CONSECUTIVE_INVALID=2、MAX_CONSECUTIVE_NO_PROGRESS=4(`tool_calling.py`)。

### 4.4 Facts/Policy 根因判断

- `app/agent/facts.py`:`evaluate_facts` 证据键→Fact 键映射(E1→F_ENDPOINT_DEGRADED 等;L1/L2 复合 F_BLOCKER_CONFIRMED)。
- `app/agent/policies.py`:`evaluate_policies` 双 Policy(SCN-001/SCN-002)+ `decide_root_cause`;`evaluate_exclusions`(X-NO-TARGET-LOCK-WAIT 排除项)。
- 根因确认 → `propose_fix`(`nodes.py`)→ `FixRegistry.build_proposal`(`fix_registry.py`),完全确定性、零 LLM 调用。

### 4.5 Proposal 与 Approval

- `propose_fix` 预创建 proposal + approval(`proposal_repo`/`approval_repo`),状态进入 awaiting_approval。
- `human_approval`(`nodes.py:681`):`interrupt()` 挂起。
- 审批接口:`api/approvals.py:decide`(decision=approved/rejected),校验 pending/未过期,服务端确定 approver 身份。

### 4.6 写操作执行

- `execute_fix`(`nodes.py`):确定性节点,六项校验 + 幂等(`fix_execution.idempotency_key` 唯一约束)+ no_op 兼容。
- 锁场景:`session_terminator` 执行器(KILL 前复核)。

### 4.7 恢复验证

- `verify_recovery_node`(`nodes.py:811`):按根因分发(锁根因 → 目标范围六项验证;其他 → verify_recovery 工具)。
- 未恢复 → `reflect` 反思重试(≤3 轮)。

### 4.8 Replay 与报告

- 回放写入:`app/replay/writer.py`(ReplayWriter);快照:`app/replay/snapshot.py`;投影:`app/replay/projector.py`。
- 版本:`app/replay/versions.py`(POLICY_BUNDLE_VERSION)。
- 报告:`report` 节点 → `postmortem_repo`;前端 ReportView。

---

## 5. LangGraph 状态与恢复机制

### 5.1 图节点与条件边

见 §4.3;路由函数 `_after_diagnose/_after_approval/_after_verify_recovery/_after_reflect`(`graph.py`)。

### 5.2 State 核心字段(`state.py`,TypedDict)

`incident_id/run_id/thread_id/status/hypotheses/evidence/evidence_gate/policy/facts/root_cause_code/fix_proposal/approval/fix_execution/recovery/report/termination_reason/reflection_log/reflection_count` 等(完整见 `state.py`)。

### 5.3 thread_id/checkpoint

- thread_id 在 `run_repo.create_run` 生成(`run-{uuid4}`),`agent_run.thread_id` 唯一。
- checkpointer:`runner.get_saver()`(同步 `SqliteSaver`,`check_same_thread=False`),图经 `asyncio.to_thread` 在线程池执行。
- 恢复:`Command(resume=...)` + 同一 thread_id(`runner.resume_investigation`)。

### 5.4 interrupt / 审批恢复 / 进程重启

- 审批挂起:`human_approval` 的 `interrupt(...)`。
- 审批恢复:`api/approvals.py:decide` → `resume_investigation(runs[0].thread_id, {decision, comment})`。
- 进程重启:`runner.recover_pending_runs()`(启动时扫描 pending runs 从 checkpoint 恢复)。
- 版本冻结:恢复前校验 `expected_policy_bundle_version` ≠ 当前 → `version_mismatch`(`runner.resume_investigation`)。

### 5.5 已知限制(待核查)

- 审批恢复用 `runs[0]`(incident 最近一次 run 的 thread_id)绑定,若 incident 有多个 run,恢复目标是否准确需核查(`approvals.py:71-73`)。
- 单 ai-service 实例(内存 `_tasks` dict 不跨进程;checkpoint 在本地 sqlite 文件,非共享存储)——多实例部署未支持。

---

## 6. 数据库模型和迁移

### 6.1 migration

- 目录:`scripts/db/migrations/`,迁移器 `scripts/db/migrate.py`(checksum 校验/幂等/Advisory Lock)。
- 当前文件:002_business_schema / 003_users_roles / 004_control_schema / 005_v12_mcp_migration / 006_v13_lock_tables / 008_tool_call_attempt。
- **无 001**(001 未见,起始为 002);**007 缺失**(从 006 跳到 008,历史迁移编号有断层,属正常,勿补号)。

### 6.2 核心表(`app/db/models.py`)

`incident / agent_run / hypothesis / evidence / hypothesis_evidence / tool_call / tool_call_attempt / fix_definition / fix_proposal / approval / fix_execution / recovery_check / postmortem / incident_event / incident_replay_step / model_call / retrieval_record / eval_run` 等(control 库)。

### 6.3 状态字段与含义

- incident.status:created / investigating / awaiting_approval / recovered / needs_human / rejected / failed。
- approval.status:pending / approved / rejected。
- fix_execution.status:pending / succeeded / failed(含 idempotency_key 唯一约束)。
- agent_run.status:created / investigating / recovered / needs_human / rejected / failed / cancelled(终态集见 `runner.py:_finalize_run`)。

### 6.4 幂等/唯一约束/审批绑定

- `fix_execution.idempotency_key` 唯一 → 写操作幂等。
- `agent_run.thread_id` 唯一。
- approval 绑定:`approval.incident_id + fix_proposal_id + parameters_hash`;fix_execution 绑定 `approval_id + fix_proposal_id`。

### 6.5 时区语义

- `app/db/models.py:utcnow()` = `datetime.now(timezone.utc).replace(tzinfo=None)` → **存储 naive UTC**(无时区信息)。
- 前端展示本地时间;跨时区比较需注意(待核查是否有依赖本地时区的地方)。

### 6.6 历史 migration 禁止修改

已有 checksum 已记录于 `schema_migrations` 表;改动任何已应用 migration 会导致 `migrate.py` 拒绝(checksum 变更)。

---

## 7. MCP 与工具清单

### 7.1 工具全集(7 个只读,`app/mcp/contract.py:14`)

| 工具 | 输入要点 | 数据来源 | 只读 |
|---|---|---|---|
| get_service_metrics | service_ref(order-service/inventory-service) | Prometheus(真实)/fixture | ✅ |
| get_trace | trace_ref(REPRESENTATIVE_SLOW_TRACE)/trace_id | Jaeger(真实)/fixture | ✅ |
| list_expensive_query_digests | query_ref(INVENTORY_LOOKUP) | performance_schema(慢查询 digest) | ✅ |
| get_query_plan | table_ref(inventory) | information_schema/EXPLAIN | ✅ |
| get_index_info | table_ref(inventory) | information_schema | ✅ |
| get_lock_waiters | scope_ref(INVENTORY_RESERVATION) | performance_schema.data_lock_waits/data_locks/threads | ✅ |
| get_transaction_details | transaction_ref(OBSERVED_BLOCKER/blk_\d+) | information_schema.innodb_trx | ✅ |

### 7.2 参数白名单(`app/tools_core/schemas.py`)

- service_ref 固定枚举;query_ref/table_ref/scope_ref 白名单模板;数值参数有 ge/le 边界。
- incident_id/agent_run_id 由 MCP Client 从可信上下文注入,LLM 侧 Schema 隐藏。

### 7.3 权限账号(`scripts/db/migrations/003_users_roles.sql`)

5 个权限角色(role_*),账号映射自角色:

| 角色 | 权限 | 用途 |
|---|---|---|
| role_control_app | control 库 CRUD | 审计/事件写入 |
| role_app_business | business CRUD + INDEX | 业务读写/场景控制 |
| role_ai_investigator | business SELECT + performance_schema SELECT + PROCESS | 只读调查 |
| role_fix_executor | business INDEX | execute_fix 专用 |
| role_session_terminator | performance_schema SELECT + PROCESS + CONNECTION_ADMIN | KILL 会话 |

### 7.4 缓存/实时

- fixture 模式(`TRACEMIND_METRICS_BACKEND=fixture`)用 fixture 数据,不进真实观测。
- 真实模式必须实时(观测数据有时间窗口);`get_lock_waiters` 有 `snapshot_expires_at` 过期语义(V1.3)。

---

## 8. 故障场景

### 8.1 SCN-001(缺索引)

- 注入:删除 `idx_sku_warehouse`(`ScenarioService.inject` → `DROP INDEX idx_sku_warehouse ON inventory`)。症状:库存查询退化为全表扫描,P95 从 ~2ms 升到 ~120ms。
- reset:重建索引(`CREATE INDEX idx_sku_warehouse ...`)+ 清理观测缓存/场景状态。
- 证据链:E1(P95 异常)→ E2(trace 数据库阶段主导)→ E3(慢查询 digest)→ E4(EXPLAIN 全表扫描)→ E5(索引元数据缺失)。
- Policy:五个 Fact 全 confirmed → `MISSING_INVENTORY_INDEX`。
- 处置:`CREATE_INVENTORY_INDEX`(审批后由 execute_fix 执行)。
- Preflight:DDL 六项校验 + 幂等。Verifier:索引存在 + EXPLAIN 走索引 + P95 回落。
- 测试:`ai-service/tests/` 索引类 fixture + `scripts/verify-m5.py`(SCN-001 全链路)。

### 8.2 SCN-002(长事务持锁)

- 注入:后台连接 `SELECT ... FOR UPDATE` 持锁并保持(`ScenarioService`)。症状:库存预占 `FOR SHARE` 被阻塞。
- reset:ROLLBACK + 关闭持锁连接(不改业务数据,幂等)。
- 证据链:L1(锁等待关系)→ L2(阻塞事务详情)→ L3(阻塞者匹配)→ L4(长事务)→ L5(复合)→ L6(会话状态)。
- Policy:L 链 Fact → `LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION`。
- 处置:`TERMINATE_BLOCKING_SESSION`(KILL 阻塞会话)。
- Preflight(session_terminator):审批有效未过期 / blocking_transaction_id 一致 / 仍持锁 / 账号白名单 / 非系统线程。
- 负例:审批过期、目标漂移(processlist 复用/事务不匹配)、锁已释放(stale)、非白名单账号、系统线程——均有测试(`test_session_terminator.py`/`test_session_terminator_execute.py`,共 22 个用例)。
- 测试:`scripts/verify-m13-scn002.py`。

### 8.3 演示/验收脚本

`scripts/verify-m2.py` / `verify-m3.py` / `verify-m3-expiry.py` / `verify-m5.py` / `verify-m13-scn002.py` / `verify-m14.py` / `verify-m15.py` / `verify-m17.py` / `verify-observability-resilience.py` / `verify-grafana-smoke.py` / `eval_agent.py` / `eval_agent_report.py`。

---

## 9. 安全边界

| 项 | 现状 |
|---|---|
| LLM 可做 | 提出假设、选择只读调查工具、生成复盘报告 |
| LLM 不可做 | 确认根因(确定性 Policy 决定)、执行任何写操作、任意 SQL/Shell/表名 |
| 只读/写账号 | 见 §7.3;LLM/MCP 只用只读账号 |
| 审批过期 | `approval.expires_at` 检查 + 过期扫描(`services/approval_scanner.py`) |
| 参数校验 | Pydantic 白名单(schemas.py) |
| KILL 前复核 | session_terminator 5 项复核 |
| 幂等 | fix_execution.idempotency_key 唯一 |
| 审计 | 每次工具调用(tool_call/tool_call_attempt)、模型调用(model_call)、检索(retrieval_record)、事件(incident_event)落库 |

**已确认的安全缺口(待核查项)**:
- 审批恢复用 `runs[0]` 绑定,多 run 场景可能恢复错目标。
- checkpoint 本地 sqlite 单实例,无多进程隔离。

---

## 10. 配置与启动

### 10.1 从干净克隆启动

```bash
# 1) 环境变量示例
cp .env.example .env.local   # 填 TRACEMIND_* 真实值(本地)
# 2) 数据库(本地 Windows MySQL 或 compose)
powershell -ExecutionPolicy Bypass -File scripts/init-database.ps1
powershell -ExecutionPolicy Bypass -File scripts/generate-data.ps1
# 或 compose 一键
docker compose up -d --build
# 3) AI 服务
cd ai-service && uv run uvicorn app.main:app --port 8000
# 4) 前端
cd web && npm run dev
```

### 10.2 必需/可选环境变量

- 必需:TRACEMIND_CONTROL_DB_URL、TRACEMIND_READONLY_DB_URL、TRACEMIND_SESSION_TERMINATOR_DB_URL(处置 KILL 必需)、TRACEMIND_LLM_MODE。
- 可选:TRACEMIND_LLM_API_KEY / TRACEMIND_CHAT_*(真实模型)、TRACEMIND_EMBEDDING_*、TRACEMIND_DYNAMIC_ROUTING(默认关)、TRACEMIND_COST_BUDGET(默认关)、TRACEMIND_CASE_RETENTION_DAYS(默认关)。
- 完整见 `.env.example` 与 `app/config/settings.py`。

### 10.3 不应提交的文件

`.env`、`.env.local`、`.env.vm`(gitignore 已排除);`data/checkpoints.sqlite`(运行产物)。

### 10.4 模型模式

| 模式 | 行为 |
|---|---|
| fake | FakeLLM 确定性,不触网(测试/回归) |
| real_strict | 模型失败即转 needs_human,禁止降级(正式验收) |
| real_demo | 模型失败降级确定性组件并标记 degraded(演示) |

### 10.5 公开仓库缺失/本地生成文件

- `.env.local` / `.env.vm`(密钥不进库,本地生成)。
- `data/` 下运行数据(checkpoint、观测缓存)。
- 真实 LLM key 由使用者自备(阿里云百炼,额度有限)。

---

## 11. 测试与验收

| 层 | 命令 | 验证内容 |
|---|---|---|
| Python 后端 | `cd ai-service && .venv/Scripts/pytest.exe tests/ -q` | Agent 图/工具/MCP/API/记忆/路由/评测/安全(436 个测试函数,最近一次实测通过,见下文) |
| 前端 | `cd web && npx vitest run` | Vue 组件/组合式函数(46 个用例,最近一次实测通过) |
| 前端类型+构建 | `npx vue-tsc --noEmit && npx vite build` | 类型检查 + 产物构建(最近一次通过) |
| Java | `cd java && mvn test` | JUnit5 + Mockito(单元) |
| Java 集成 | `cd java && mvn verify` | Testcontainers MySQL(需 Docker) |
| 离线评测 | `cd ai-service && TRACEMIND_RUN_PROFILE=offline_eval TRACEMIND_LLM_MODE=fake TRACEMIND_EVAL_MODE=true .venv/Scripts/python.exe ../scripts/eval_agent.py --mode offline --llm fake --runs 1` | 24 条离线 fixture |
| SCN 全链路 | `python scripts/verify-m5.py --base http://localhost:8000 --order http://localhost:8081` | SCN-001 闭环 |
| 锁场景 | `python scripts/verify-m13-scn002.py --base http://localhost:8000` | SCN-002 闭环 |
| 真实模型 | `python scripts/verify-m14.py --base http://<host>:8000 --order http://<host>:8081` | real_strict 真实模型(耗额度) |

**最近一次实测(记录于会话)**:后端 436 passed、前端 46 passed、vue-tsc+build 通过、SCN-001/002 真实模型各 3 轮全 recovered(平均 36.4s)。**这些是历史实测记录;新 Agent 接手应重新执行以当前代码验证,未执行前标记为"未验证"。**

---

## 12. 当前已知问题(待核查清单)

> V2.0-A(2026-09-02)处置结果随行标注。

| 问题 | 代码证据 | 影响 | 测试覆盖 | 推荐处理 |
|---|---|---|---|---|
| ~~审批恢复用 `runs[0]` 绑定~~ | ~~`api/approvals.py:71-73`~~ | ~~多 run 恢复错目标~~ | **已修复(V2.0-A)**:`approval.agent_run_id` 绑定准确 Run(009 迁移 + `decide_approval_cas` + scanner 绑定;legacy NULL 行回退旧行为);`tests/test_approval_cas.py` | 关闭 |
| checkpoint 本地 sqlite 单实例 | `runner.get_saver()` | 多实例部署不支持 | `tests/test_checkpoint_contract.py`(重启恢复语义已钉) | 保持单实例声明;多副本须先换共享 checkpointer |
| ~~时间字段 naive UTC / 8 小时时差~~ | ~~compose TZ=Asia/Shanghai + DB NOW()~~ | ~~审批/审计时间漂移~~ | **已修复(V2.0-A)**:compose/JDBC 全 UTC,引擎会话 `SET time_zone='+00:00'`,审计/migrate/eval_run 应用侧 UTC,审批 CAS 用应用 `now_utc`;`tests/test_utc_semantics.py` + `scripts/audit_time_sources.py`(本机实测发现 28800s 偏差,现已消除来源) | 关闭 |
| ~~未知 root cause 回退默认~~ | ~~`fix_registry.py:97`~~ | ~~未知根因按 SCN-001 处置~~ | **已修复(V2.0-A)**:未知/缺失根因与未知 action 全部 fail closed(还发现并修复 `FixProposal` 无 action_type 列导致 `fix_service` 恒默认建索引的缺陷);`tests/test_fail_closed_proposal.py` | 关闭 |
| ~~观测栈 profile 不默认启动~~ | `compose.yml` observability-ui | 全栈验收需手动拉起 | 文档已注明 + `scripts/vm-infra-check.sh` | 关闭(设计意图) |
| 成本/记忆/反思无 A/B 对照 | 无 | 简历中已降级表述 | 无 | 需要时补对照实验 |
| trace 服务读 Incident 行取观测窗口 | `trace_service.py` | 运行中 Incident 更新影响 trace 查询窗口 | 未覆盖(V2.0-A 冻结的是 Agent 侧 Run 上下文) | V2.2 统一证据/窗口数据平面时收敛 |
| Jaeger search_traces 忽略 operation_ref | `jaeger_client.py:34-52` | trace 搜索只按 service(真实验收下工作,但 operation 过滤是装饰) | 未覆盖 | V2.2 改造代表 trace 选择时一并处理(需真实后端确认 span operation 名) |
| `OPERATION_TO_URI/ROUTE` 此前为死代码且 ORDER_CREATE 映射错误 | 真实端点 `/api/orders/{orderId}/check-stock` | 真实 Prometheus 查询会空结果(fixture 掩盖) | **已修复(V2.0-A)**:映射修正 + `uri=~".+"` 通配删除 + 契约测试(`test_observability_contract.py`,含可选 live 冒烟) | 关闭(live 后端契约仍需在拉起观测栈后跑一次冒烟确认) |

## 12A. V2.0-A 基线可信化(2026-09-02)变更摘要

**目标**:不改变 SCN-001/002 诊断/审批/执行/验证/Replay/SSE 行为,把基线做实。实施计划:`docs/superpowers/plans/2026-09-02-v2.0-a-baseline-trust.md`。

- **migration 009**(`009_v20_run_baseline.sql`,追加式):`agent_run.run_context_snapshot_json / checkpoint_thread_id(唯一) / checkpoint_namespace / capability/prompt/tool_bundle_version`;`approval.agent_run_id`;`fix_proposal.action_type`(存量按 fix_definition 回填)。已在真实库增量升级验证(备份先行)+ VM scratch 空库从零初始化验证(官方迁移器)。
- **Run 上下文冻结**:Run 创建事务内冻结不可变 `RunContextSnapshot`(`app/services/run_context.py`);`runner` 启动/恢复只读快照;快照缺失/损坏 → fail closed(`context_snapshot_invalid`);`incident.service_ref` 缺失 → 拒绝创建 Run(删除 "inventory-service"/"medium" 默认兜底)。
- **版本冻结前移**:bundle 版本在 Run 创建事务写入;`resume_investigation` 的 `version_mismatch` 校验对未完成 Run 真正生效(原实现在收尾补写、校验是空操作);收尾 `freeze_run_versions` 不再覆盖已冻结值。
- **审批安全**:`decide_approval_cas` 原子 CAS(pending + 未过期,应用生成 `now_utc`);审批决定/回放/过期扫描按 `approval.agent_run_id` 恢复准确 Run(仅 legacy NULL 回退旧行为);并发双批准只有一个成功。
- **checkpoint 契约**:`runner` 改用 LangGraph 文档契约 `{"configurable": {"thread_id": ...}}`;真实 SqliteSaver 集成测试钉死 thread_id 层级、进程重启 resume、thread↔checkpoint 一致(探针证实:旧扁平写法靠 `ensure_config` 归一化才工作)。
- **UTC 统一**:compose/CI/JDBC 全 UTC;全部引擎会话 `SET time_zone='+00:00'`;`audit_repository`/`migrate.py`/`eval_run` 改应用侧 naive UTC;新增 `scripts/audit_time_sources.py`(只读审计,本机曾实测 DB NOW() 与 UTC 差 28800s)。
- **可观测性契约**:`OPERATION_TO_URI/ROUTE` 修正为真实模板(ORDER_CREATE → `/api/orders/{orderId}/check-stock`),新增 `SERVICE_TO_OPERATIONS`/`uri_regex_for_service`(未知 service fail closed);`PrometheusMetricsClient` 删除 `uri=~".+"` 通配;`search_traces` 仍按 service(V2.2 收敛,见 §12)。
- **干净克隆门禁**:compose 删除废弃 `scripts/sql` 挂载,新增 `db-init` 一次性服务(官方迁移器,幂等);`.env.vm.example` / `secrets/mcp_clients.example.json` 入库,`.gitignore` 排除真实值;`scripts/bootstrap-dev.ps1` / `.sh` 生成忽略文件 + 迁移 + 依赖校验;`scripts/vm-infra-check.sh` 校验 VM 基础设施。VM 实测:干净目录 bootstrap 后 `docker compose config` 通过。
- **杂项修复**:`write_observation_query` 引用不存在的 `created_at` 列(死代码路径,已对齐 004 schema);`update_run_status` 终态集合补 `cancelled`。

**新增测试**:行为特征(10)、fail closed(6)、审批 CAS/绑定(5)、UTC 语义(4)、Run 快照/版本冻结(6)、checkpoint 契约(3)、可观测性契约(6,1 个 live 冒烟按需 skip)、compose 门禁(6)、009 内容(5)。基线 436 个既有测试全部保持通过。

---

## 13. 历史设计取舍(设计意图,非当前实现事实)

- **LLM 不确认根因**:LLM 幻觉风险高;根因由真实证据 + 确定性 Policy 判定,LLM 只提出假设和选工具。
- **写操作不暴露为 MCP 工具**:MCP 只读调查;DDL/KILL 保留在确定性控制节点,审批后才执行。
- **人工审批**:写路径是风险点,必须人工确认;支持过期自动拒绝。
- **确定性 Policy 保留**:可复现、可测试、可解释,不依赖模型稳定性。
- **单 ai-service 实例**:checkpoint 本地存储 + 内存 task 管理,简化部署;多实例是后续演进。

---

## 14. 术语表

| 术语 | 含义 |
|---|---|
| Incident | 一次故障事件(含标题/服务/严重度/状态) |
| AgentRun | 一次诊断运行(thread_id 唯一,对应 LangGraph 一次执行) |
| Evidence | 证据项(E1~E5/L1~L6),含 source/key/passed/content |
| Fact | 由证据键映射的布尔事实(F_ENDPOINT_DEGRADED 等) |
| Policy | 判定根因的策略(SCN-001/SCN-002) |
| Proposal | 修复提案(确定性生成,含 action_type/parameters_hash) |
| Approval | 审批记录(pending/approved/rejected,含过期时间) |
| Execution | 写操作执行(fix_execution,幂等) |
| Replay | 不可变回放(incident_replay_step 纯追加) |
| real_strict | 真实模型严格模式(禁止确定性兜底) |

---

## 15. 新 Agent 接手清单

接手后、开始任何 V2 升级前,按顺序完成:

1. [ ] `git pull` 确认最新;记录 `git rev-parse HEAD`。
2. [ ] 复读本文档,与当前代码 diff,更新过期信息。
3. [ ] 跑后端全量测试(`pytest tests/ -q`),确认绿。
4. [ ] 跑前端全量(`npx vitest run`)+ 类型检查 + build。
5. [ ] 若需真实验收:确认 VM/本地 MySQL、观测栈(observability-ui profile)、qdrant collection(重建 runbook 索引,见 V1.10 记忆)齐备。
6. [ ] 检查 `.env.local` 是否有真实 LLM key(未配置则真实模式不可用)。
7. [ ] 更新本文档"文档快照信息"与"最近一次实测"。
8. [ ] 核对 §12 已知问题清单,确认哪些已修复/仍存在。

> **不要**基于历史对话记忆直接声称能力已实现/已验证;一切以当前代码 + 重新执行测试为准。
