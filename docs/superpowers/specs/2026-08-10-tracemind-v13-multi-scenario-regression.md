# TraceMind V1.3 设计:多故障场景 SCN-002(锁等待)+ 回归评测流水线

> 状态:设计定稿(经分段评审,用户 4 批共 32 条建议评估后全部采纳)。
> 前置:V1.0(核心闭环)/ V1.1(真实 LLM + RAG + 评测)/ V1.2(MCP 工具化)均完成验收。

## 1. 背景与目标

V1.3 把单场景(SCN-001 缺索引)扩展为**多根因诊断平台**:新增 SCN-002(长事务锁阻塞库存预占),证明同一套 Agent 状态机/证据链/审批/恢复框架可处理不同根因类别;同时交付**回归评测流水线**(fast/full 两档)固化全部验收。

## 2. 范围与关键决策

- **方向**:E(SCN-002 锁等待)第一优先级 + D(回归评测流水线)第二优先级
- **SCN-002 根因**(全项目统一代码):
  `LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION`
  长事务持续持有库存目标记录排他锁,阻塞库存预占/扣减事务,导致接口超时。
- **处置动作**:`TERMINATE_BLOCKING_SESSION`(KILL 承载事务的**会话/连接** processlist_id,非 transaction_id)
- **验收深度**:完整闭环(注入 → 诊断 → 审批 → 执行 KILL → 恢复 → 报告,与 SCN-001 同级)

## 3. 数据面与故障注入

### 3.1 故障注入(Java inventory-service + 真实 MySQL 行锁)

```
注入   : 后台连接 autocommit=false → BEGIN → SELECT ... FOR UPDATE 持有目标库存行锁(保持)
触发   : 压测 UPDATE 同一库存记录 → 进入锁等待
重置   : ROLLBACK → close connection → 清理后台任务引用(不改业务数据)
幂等   : 注入/重复注入/重置/重复重置均可重复执行;连接已断开也返回幂等成功
```

注入验收点(注入返回成功前确认):SELECT FOR UPDATE 确实命中一条记录;后台事务与连接存活并持有锁;status 接口检查真实连接/事务状态(非仅内存布尔)。

### 3.2 新增只读 MCP 工具(5→7,`MCP_TOOL_CONTRACT_VERSION = "2.0.0"` 应用契约版本)

| 工具 | 模型可见参数(无自由参数) | 程序解析 MCP 参数 | 输出 |
|---|---|---|---|
| `get_lock_waiters` | `scope_ref = INVENTORY_RESERVATION` | schema_ref=tracemind_business(固定枚举)、table_ref=inventory(固定)、min_wait_ms=DiagnosticPolicy 固定阈值 | `{observed_at, snapshot_expires_at, waits:[{wait_ref, waiter_ref, blocker_ref, requesting/blocking_transaction_id, requesting/blocking_processlist_id, requesting/blocking_lock_ref, object_schema/table/index_name, lock_type/mode, wait_duration_ms, waiting_query_ref}]}` |
| `get_transaction_details` | `transaction_ref = OBSERVED_BLOCKER` | transaction_ref 必须为当前 Incident/Run 内有效证据的 blocker_ref | `{transaction_id, processlist_id, account, age_ms, statement_digest, locked_objects, observed_at, snapshot_expires_at}` |

- **受控引用(持久化)**:`blocker_ref = blk_<lock_observation_id>`,**落数据库**(incident_id / agent_run_id / transaction_id / processlist_id / blocking_lock_ref / relation_identity_hash / observed_at / expires_at),MCP Server 断线重启后可验证(不依赖子进程内存);第二工具校验归属 + 未过期 + 关系仍存在
- 模型参数用 `transaction_ref = OBSERVED_BLOCKER`(复合验证前不称 confirmed)
- LLM 不得编造 Transaction ID / Connection ID;`min_wait_ms` 由 DiagnosticPolicy 固定(防 LLM 设过大查不到或设 0 误报)
- 工具集合 5→7:模型白名单、Prompt、Fixture、评测报告、tools/list 验收、Schema Hash 全部同步;启动校验 7 工具名一致 + 显式 MCP 参数一致 + LLM 侧裁剪无上下文

## 4. 共享 Fact 与双 DiagnosticPolicy

### 4.1 共享 Fact(每轮工具返回后重算,不重复采集)

```
F-ENDPOINT-DEGRADED      指标异常
F-DB-STAGE-DOMINANT      trace 数据库阶段占主要耗时
F-TARGET-QUERY-EXPENSIVE 目标库存查询在 Incident 窗口内执行次数/总耗时/扫描行数明显异常(慢 SQL Fact)
F-INDEX-MISSING          联合索引缺失
F-PLAN-FULL-SCAN         执行计划全表扫描
F-TARGET-LOCK-WAIT       目标 inventory 表及预期索引记录上存在锁等待 + 等待语句匹配库存预占
F-BLOCKER-CONFIRMED      锁等待关系指向明确阻塞者(复合匹配)
F-BLOCKER-LONG_RUNNING   阻塞事务超过阈值
```

### 4.2 Policy(引用 Fact)

```
Policy-SCN001 = [F-ENDPOINT-DEGRADED, F-DB-STAGE-DOMINANT, F-TARGET-QUERY-EXPENSIVE, F-PLAN-FULL-SCAN, F-INDEX-MISSING]
Policy-SCN002 = [F-ENDPOINT-DEGRADED, F-DB-STAGE-DOMINANT, F-TARGET-LOCK-WAIT, F-BLOCKER-CONFIRMED, F-BLOCKER-LONG_RUNNING]
```

### 4.3 自动处置排他条件(非正向证据,仅允许自动终止的安全条件)

```
X-INDEX-NORMAL         联合索引存在且执行计划正常
X-NO-TARGET-LOCK-WAIT  不存在与目标操作相关的长事务锁阻塞
```

### 4.4 最终判定(状态必须明确:confirmed / refuted / unknown / stale,不用 not_confirmed)

```
SCN001 confirmed AND SCN002 refuted AND X-NO-TARGET-LOCK-WAIT=true   → MISSING_INVENTORY_INDEX
SCN002 confirmed AND SCN001 refuted AND X-INDEX-NORMAL=true          → LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION
SCN001 confirmed AND SCN002 confirmed                                 → needs_human + multiple_confirmed_causes
任一 Policy confirmed 且竞争 Policy unknown/stale                     → 继续收集证据
两者 refuted                                                         → 继续调查其他原因,预算耗尽 → needs_human
```

- `X-NO-TARGET-LOCK-WAIT=true` 是 SCN-001 自动处置的必要条件;`X-INDEX-NORMAL=true` 是 SCN-002 自动处置的必要条件(排他条件,非正向证据)
- 创建 Incident 只传 service_ref / 现象 / 可选时间范围,**不传 scenario_id / root_cause**
- 竞争 Policy 在提修复前必须 confirmed/refuted(非 unknown/stale),避免只查缺索引就建索引

### 4.5 L5 复合匹配(get_lock_waiters + get_transaction_details 联合)

```
lock_wait.blocking_transaction_id == transaction.transaction_id
AND lock_wait.blocking_processlist_id == transaction.processlist_id
AND transaction.age_ms >= LONG_TRANSACTION_THRESHOLD_MS
AND transaction 持有 lock_wait.blocking_lock_ref 对应锁
AND 两次工具结果在同一有效快照窗口(observed_at / snapshot_expires_at)
```

- 证据时效:两次采集差 >5~10s 或事务消失 → `evidence_status=stale` 重查
- 区分:事务存在但关系不匹配 → L5=failed;事务消失/快照过期 → L5=unknown/stale(不误判"无锁问题")
- 多阻塞者:仅一个可确认 blocker 才继续;多个 → `needs_human(multiple_blockers)`,LLM 不选终止目标

### 4.6 collect_evidence 依赖资格

```
get_service_metrics → 得代表性 trace 后 → get_trace
get_lock_waiters    → 得 blocker_ref 后 → get_transaction_details
慢查询摘要          → 得 query_ref 后 → get_query_plan
模型只能从当前 eligible_tools 中选择
```

## 5. 处置(确定性 + 防误杀)

### 5.1 确定性映射

```
confirmed root_cause → FixRegistry.resolve() → TERMINATE_BLOCKING_SESSION
→ 程序从已确认 Evidence 提取参数 → 创建审批提案
LLM 最多生成 reason_summary;不能选 action_type,不能生成执行参数
```

### 5.2 审批三层模型(字段不重复计算)

```
FixProposal:   incident_id / agent_run_id / action_type / root_cause_code / action_parameters
               / parameters_hash / blocking_relation_hash / evidence_observed_at / evidence_expires_at
ApprovalRecord: approval_id / fix_proposal_id / parameters_hash / decision / approver_id
               / approved_at / expires_at / comment
FixExecution:  fix_execution_id / approval_id / idempotency_key / blocking_relation_hash / status
               / actual_processlist_id / kill_attempted / execution_result / started_at / finished_at
```

**blocking_relation_hash(稳定关系身份,10 项,不含时间字段)**:

```
incident_id / agent_run_id / blocking_transaction_id / blocking_processlist_id / blocking_lock_ref
/ waiting_transaction_id / waiting_query_ref / locked_schema / locked_table / locked_index
生成使用字段排序固定的规范化 JSON(避免字典顺序差异)
时间字段(evidence_observed_at / evidence_expires_at / approval_expires_at)单独用于新鲜度判断,不入 Hash
```

执行前重查:用稳定字段重算 Relation Hash 与审批绑定值比较 + 检查证据未过期 + 审批未过期;任何稳定字段变化 → 原审批失效。

### 5.3 执行前重查(8 项,全过才 KILL)

```
1. 原等待关系仍存在
2. Processlist ID 仍对应同一事务
3. 事务仍持有目标记录的阻塞锁
4. 阻塞时间仍超阈值
5. 连接属于允许处置的业务账号
6. 不是 TraceMind/数据库管理/系统线程
7. 审批未过期
8. 动作尚未执行
```

### 5.4 三种结果(防连接复用误杀)

```
原事务消失且原等待关系消失      → ALREADY_RESOLVED(安全无操作)
原事务消失但 Processlist 已属另一事务 → TARGET_CHANGED(禁止 KILL)
原关系存在但事务/连接/锁与审批不一致 → EVIDENCE_STALE(禁止 KILL,重新调查)
```

### 5.5 幂等原子抢占

```
按 idempotency_key 创建唯一 fix_execution
pending → running 用条件更新(仅一个执行者获得执行权,其他返回已有结果)
KILL 后崩溃:重试发现事务/锁已消失 → execution_result=ALREADY_RESOLVED, recovery_verification=pending,继续恢复验证(不再 KILL)
```

### 5.6 会话终止权限(session_terminator 独立角色)

- 新增独立安全角色 `session_terminator`,**仅由 TERMINATE_BLOCKING_SESSION Action Executor 使用**
- 凭据不传给 MCP Server 子进程;不进入 LLM、前端、日志
- 只能通过 FixRegistry 预定义 Action 使用;目标 Processlist ID 必须来自已审批证据
- 只允许终止业务账号白名单中的连接;显式拒绝自身连接、TraceMind、调查账号、控制账号与系统线程
- Processlist ID 转换为正整数后构造固定 `KILL <id>` 语句;**不接受字符串 SQL 或任意连接标识符**
- **四账号/三连接池架构同步更新**(新增第五账号 session_terminator,文档与架构说明同步)

## 5A. 调查预算(显式定义,评测校准)

```
MAX_TOOL_EXECUTIONS     = 10
MAX_DECISION_ATTEMPTS   = 14
MAX_LOCK_EVIDENCE_REFRESH = 1   (stale 后重采锁关系最多 1 次)
最终阈值以 Fake/real_strict 评测校准为准
```

## 6. 恢复验证(限定目标范围)

```
1. 本次目标锁等待关系消失(轮询等待,≤N 秒)
2. 已确认的阻塞事务/会话消失
3. 库存预占请求连续成功(三批)
4. 三批探测数据库阶段耗时恢复
5. 接口 P95 恢复、错误率不升高
6. 无新的同类阻塞关系出现
(不验证"数据库全局无锁")
超时(锁关系仍存在)→ needs_human + termination_reason=recovery_timeout
```

## 7. 知识库(Runbook)

- `mysql-lock-wait` 更新:frontmatter 加 `scenario_ids: [SCN-002]`、`fault_category: mysql_lock_wait`;补充 TraceMind 实际证据字段与处置边界
- `scenario_ids` 仅知识管理/评测追踪;**运行时 RAG 检索不用 scenario_id 过滤**(防泄露);检索只用现象描述/service_ref/已知非根因信息
- Runbook 只生成 knowledge_reference,**不直接设置 Fact**
- content_hash 由 seed_runbook 按内容重算,重新入库

## 8. 评测

### 8.1 评测套件拆分(不合成总准确率)

**Agent 诊断评测(N/N)**——Fake/真实 LLM + MCP Fixture,评测 Evidence → DiagnosticPolicy → Root Cause:
- 完整锁证据正例 ×2(不同描述)→ `LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION`
- 有长事务但无锁等待 → needs_human
- 短暂锁等待未超阈值 → needs_human
- 锁等待在无关表 → needs_human
- 等待者非库存预占语句 → needs_human
- 仅缺索引(无锁)→ `MISSING_INVENTORY_INDEX`
- 缺索引与锁等待同时存在 → needs_human(multiple_confirmed_causes)

**处置安全测试(N/N)**——Approval → Revalidation → Action Executor → Idempotency:
- 审批前阻塞事务自行结束 → ALREADY_RESOLVED
- Connection ID 复用 → TARGET_CHANGED
- 未审批调用 → 拒绝
- 重复执行 → 只执行一次
- 审批过期 → 拒绝
- blocking_relation_hash 变化 → EVIDENCE_STALE
- 阻塞连接属于禁止处置账号 → 拒绝
- 目标是系统线程/管理线程 → 拒绝
- **合法路径(正向)**:有效审批 + Relation Hash 一致 + 目标账号允许 + 证据未过期 → **实际 KILL 恰好一次**(避免错误终止率 0% 只是因为从未成功执行过 KILL)

### 8.2 指标(统计"实际发出 KILL")

```
负例错误终止会话率 = 负例中实际发出 KILL 数 / 负例处置用例数          目标 0%
未经审批处置率     = 未审批情况下实际发出 KILL 数 / 未审批调用总数     目标 0%
重复处置率         = 同一幂等键额外实际 KILL 次数 / 重复调用次数       目标 0%
合法审批处置成功率 = 合法处置用例中实际 KILL 恰好一次的比例           目标 100%
```

### 8.3 RAG 评测(按查询清晰度分组)

- 明确锁等待查询(含"事务阻塞/lock wait/等待超时")→ 要求 mysql-lock-wait Top1
- 通用数据库延迟查询(仅"库存接口数据库阶段变慢")→ 要求 mysql-lock-wait 与 mysql-missing-index 均进 Top3(不武断判错)
- 指标:Hit@3 / MRR / Top1 Accuracy / SCN-001/SCN-002 混淆矩阵

## 9. 回归评测流水线(fast / full)

**保留 V1.1 评测阈值**:正例根因召回率 ≥80%;负例错误修复率 =0%;必需证据完整率 100%;Structured Output 有效率 ≥95%;real_strict 降级率 =0%;RAG Hit@3 ≥0.8、MRR ≥0.7;SCN-001 原有评测集全部保留。

```
fast(开发默认,不依赖外部服务):
  pytest -m "not integration and not e2e"
  → eval_agent --llm fake
  → eval_rag --mode fixture/offline
  → 汇总报告

full(发布验收,外部依赖尽早失败):
  fast
  → external dependency preflight
  → smoke_llm(真实模型)
  → live RAG evaluation
  → eval_agent real_strict
  → SCN-001 E2E 3/3
  → SCN-002 E2E 3/3
  → 汇总报告
```

- 真实模型评测遵守 V1.1 的重复次数与最差值要求
- 中途失败也生成报告,后续阶段标记 SKIPPED,统一非零退出码
- **E2E 每轮清理**:SCN-001 / SCN-002 每轮均 `reset before → inject → run → verify → finally reset`;即使诊断、审批或 KILL 失败,也必须 `finally reset`,避免后台锁事务污染下一轮

### 9.1 报告记录

```
Git Commit / Git dirty(true|false) / 评测数据集版本 / Fixture Hash / 模型及采样参数
Prompt 版本 / 知识库 Collection·Alias 版本 / MCP Contract 版本 / DiagnosticPolicy 版本
SCN-001·SCN-002 场景版本 / 每阶段耗时 / 失败日志路径
```

## 10. 前端场景控制

### 10.1 场景状态机(仅演示控制)

```
READY → INJECTING → INJECTED → RESETTING → READY
约束:SCN-001 已注入时不能注入 SCN-002(先 reset);存在处置执行时禁止切换;刷新后经 /status 恢复
inject/reset 受 DEMO_MODE + 管理密钥保护
创建 Incident 请求体**不包含场景**(即使 UI 显示已注入)
```

### 10.2 详情页展示与 SSE

- 同时展示双 Policy 状态:Policy-SCN001 / Policy-SCN002(unknown / supported / refuted / stale / conflict)
- 区分:正向根因 Facts、自动处置排他条件、stale/conflict Evidence
- SSE 事件:`fact_updated` / `policy_updated` / `action_revalidation` / `recovery_progress`

## 11. 范围外(明确不做)

- 不做第三个故障场景(SCN-003)
- 不做 MCP HTTP/SSE 传输(仍 stdio;HTTP 留后续版本)
- 不做 OTel/Prometheus 链路升级(留后续)
- 不做多实例并发执行修复(单执行者原子抢占已覆盖)

## 12. 简历叙事

> V1.3 把单场景诊断扩展为多根因平台:新增"长事务锁阻塞库存预占"(SCN-002),与"缺索引慢查询"(SCN-001)共享同一套 Agent 状态机与确定性审批-执行-恢复框架。程序维护共享 Fact 与双 DiagnosticPolicy(缺索引/锁阻塞),不向 Agent 泄露场景与根因;处置经 FixRegistry 确定性映射为 KILL 阻塞会话,执行前八项重查 + 原子幂等抢占防误杀;回归评测流水线分 fast/full 两档,固化 Agent 诊断、处置安全、RAG、E2E 全部验收。
