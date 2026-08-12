# V1.5 设计:证据与决策链回放(Replay)

> 分段评审定稿:方案 A(调查时快照 + 只读 API + 前端本地播放),四批共 32 条评审意见全部采纳。
> 核心原则:**回放只组合保存的历史快照,绝不重新运行** LLM / DiagnosticPolicy / Evidence Evaluator / Tool Eligibility;旧数据缺失即标记,不推测。

## 1. 背景与目标

V1.0~V1.4 已交付:核心闭环、真实 LLM+RAG+评测、MCP 工具服务、多场景 SCN-002、真实可观测性(Prometheus/Jaeger/OTel)。V1.5 补上 V1.0 设计文档就规划的"完整执行回放"——面向面试演示与复盘:

- 按调查步骤逐步回放 Agent 的**可审计外部决策依据**(不是隐藏思维链,故命名"证据与决策链")
- 回放**只读、零副作用、零重算**:不触发状态机、不调 LLM/MCP、不执行审批处置、不重算 Policy
- **前端本地播放**:一次加载 Replay 数据,播放节奏由前端驱动;后端只提供确定性只读数据

## 2. 核心设计决策

| 决策 | 理由 |
|---|---|
| 调查时写入**回放专用快照**(非完整 IncidentState,非事后重建) | 完整 IncidentState 含内部字段/大型工具返回/未脱敏参数;事后重建无法恢复 eligible_tools、决策摘要、前后状态、当时版本 |
| 快照**不可变纯追加**(两段式 = 多条 phase 记录) | 支持 started/completed/failed 两段式而保持只追加;崩溃后可识别未完成步骤 |
| Replay Projector 只组合历史事实 | Policy/LLM 升级后历史回放不变;旧 Incident 标记 partial,绝不按当前程序补算 |
| 回放以**单个 Agent Run** 为单位 | 一个 Incident 可有多个 Run,不拼接成一条时间线 |

## 3. 后端:回放快照采集

### 3.1 表 `incident_replay_step`(纯追加,无更新)

```
id, incident_id, agent_run_id, logical_step_id, phase(started/completed/failed),
round_no INT NULL, attempt_no INT DEFAULT 1, step_type, step_title, step_outcome,
sequence_no, state_before_json, state_after_json, decision_json, operation_json,
source_references_json, actual_duration_ms,
replay_schema_version, policy_bundle_version, prompt_version, tool_contract_version,
normalization_rule_version, snapshot_hash, payload_size_bytes, occurred_at
UNIQUE(agent_run_id, sequence_no)
UNIQUE(agent_run_id, logical_step_id, attempt_no, phase)
CHECK(phase IN ('started', 'completed', 'failed'))
排序:agent_run_id → sequence_no → id
```

### 3.2 logical_step_id / attempt_no / phase 关系(必须)

- **每次 LangGraph 节点实际调用生成一个新的 `logical_step_id`**。
- **同一次节点调用内部的外部请求重试**使用相同 `logical_step_id`、不同 `attempt_no`。
- 每个 Attempt 最多一条 `started` 和一条终态记录。
- **失败后重新运行整个节点 → 生成新的 `logical_step_id`**,而不是继续增加 `attempt_no`。
- Projector 先按 `(logical_step_id, attempt_no)` 组装 Attempt,再把 Attempts 组合成一个 UI 步骤。
- **`phase`(生命周期:started/completed/failed)与 `step_outcome`(业务结果:succeeded/rejected/expired/needs_human/already_resolved/target_changed)分离**——消除 `phase=completed` + `step_status=failed` 的歧义。

### 3.3 专用 DTO `ReplayStateSnapshot`(不序列化完整 IncidentState)

```
hypotheses / facts / diagnostic_policies / exclusion_conditions /
confirmed_root_cause / incident_status / pending_approval / recovery_status
```

快照生成流程(ReplaySnapshotFactory):字段白名单 → 深拷贝 → 脱敏 → 确定性排序(hypotheses 按 ID、facts/policies 按 code、evidence 引用按 ID)→ **Canonical JSON**(固定键序、固定转义)→ `snapshot_hash = SHA-256(canonical_json)`。snapshot_hash 只证明一致性,**不声称防恶意篡改**。大型工具结果**不塞进快照**:仅经 source_references 引用;限单步 payload(`payload_size_bytes`)。

### 3.4 Step 覆盖(按实际执行写入,非预设固定序列)

step_type 为**业务枚举**(不用 Python 函数名,防重构破坏兼容):

```
INCIDENT_INGESTED / HYPOTHESES_GENERATED / EVIDENCE_COLLECTION / DIAGNOSIS_EVALUATED /
FIX_PROPOSED / APPROVAL_REQUESTED / APPROVAL_DECIDED / ACTION_REVALIDATED /
FIX_EXECUTED / RECOVERY_VERIFIED / REPORT_GENERATED / RUN_TERMINATED
```

- `collect_evidence` 可执行 1~N 次;`diagnose` 可重复;审批可拒绝/过期;`execute_fix` 可从未发生;恢复验证可失败——时间线按实际调用生成。
- **审批决定来自外部 API**(非 LangGraph 节点),单独生成 `APPROVAL_DECIDED` Step。

### 3.5 写入一致性(区分两类操作)

- **纯控制库操作**(创建 FixProposal/Approval、修改 Incident 状态)→ 可与完成阶段记录**同一控制库事务**提交。
- **外部操作**(LLM/MCP/Prometheus/Jaeger 查询/KILL CONNECTION/LangGraph Checkpoint/Java 请求)→ 无法与 MySQL 回放记录同事务:提交 `started` → 执行外部操作 → 提交 `completed/failed`。
  - **KILL 流程**:`started` 持久化 → FixExecution 原子抢占 → 执行 KILL → `completed/failed` 持久化。
  - 外部操作成功但进程崩溃 → 回放显示"已开始但缺完成记录",经 `fix_execution` 审计引用展示"外部操作可能已执行",**绝不反向生成虚假 completed**。
- **序号分配与插入同事务**:`next_replay_sequence` 自增与对应 Record 插入必须在**同一数据库事务**(否则序号分配后插入失败产生空洞,被完整性检查误判 partial)。
- **logical_step_id 在外部操作开始前生成并持久化**;审批 API 重试、LangGraph 恢复或客户端重复提交时**复用原逻辑步骤或业务事件 ID**,避免生成重复的 `APPROVAL_DECIDED` 步骤(幂等)。
- **诚实边界**:Step 写入失败时**尽最大可能记录捕获错误**;`replay_status=partial` 由运行结束/读取时的完整性检查标记;数据库完全不可用时仅保留应用错误日志,不承诺写入 partial。

### 3.6 Run 级版本:冻结(预期)与实际执行分离

- Run 启动时保存 `expected_policy_bundle_version` 等**预期版本**(冻结)。
- **Step 保存该步骤实际使用的版本**(不能只是从 Run 元数据复制到 Step,否则记录的可能不是实际执行版本)。
- `prompt_version` **从实际 model_call 审计中取得**(不同节点可能使用不同 Prompt)。
- 双 DiagnosticPolicy 使用一个明确的 `policy_bundle_version`(或分别保存两个 Policy 版本)。
- **Run 恢复前校验当前实现与预期版本**:不一致 → 停止原 Run 进入 `needs_human/version_mismatch`,或创建新 Run;不允许用新实现继续旧 Run。

### 3.7 source_reference 引用不可变历史版本

Replay 只保存业务记录 ID 的话,审批/Incident/fix_execution 被更新后,回放详情会随当前数据变化,不再是历史回放。引用结构:

```json
{
  "sourceType": "tool_call", "sourceId": 123,
  "sourceVersion": 1, "sourceHash": "...",
  "capturedSummary": {}
}
```

规则:
- **优先引用只追加的审计/Event/Attempt 记录**,不直接依赖会更新的当前状态表
- 必需展示的脱敏摘要在 Step 生成时**冻结**到 `capturedSummary`
- 技术详情读取时**校验引用 Hash**;引用记录丢失或发生变化 → 标记 Replay 为 `partial`
- 引用类型:`model_call_id / retrieval_record_ids / tool_call_id / evidence_ids / observation_query_id / trace_id / approval_id / fix_proposal_id / fix_execution_id / recovery_check_id / postmortem_id`;引用目标不可用(如 Jaeger 内存 trace 丢失)→ 仍展示当时归一化 Evidence 并标记"原始对象当前不可用"

### 3.8 完整性检查 → replay_status / runStatus

Run 结束后执行一次 Replay 完整性检查,API 读取时做轻量校验。

**replay_status(回放记录完整度)**:
- `complete`:全部步骤有 completed/failed,引用有效
- `partial`:`started` 无终态且 Run 已结束 / sequence_no 异常 / source_reference 悬空或 Hash 不符 / 必需终态步骤缺失 / 快照 Schema 无法解析 / Hash 校验失败
- `partial_legacy`:V1.5 之前创建的 Incident(无完整快照),仅从已有审计投影可恢复部分步骤
- `unsupported`:快照 Schema 不兼容
- 无 Replay 数据 → `unavailable`(不是空完整回放)

**语义边界**:
- Run **尚未结束时**,孤立的 `started` 暂时属于 `in_progress`,不判 partial;Run **已结束后**仍无终态才判 partial
- `complete` **只表示回放记录完整,不表示故障已恢复**
- Manifest 必须单独返回:

```json
{
  "replayStatus": "complete",
  "runStatus": "terminated|in_progress",
  "runOutcome": "recovered|failed|rejected|needs_human",
  "terminationReason": "..."
}
```

否则"完整记录了一次失败调查"易被误读成"调查成功"。

- **"必需终态步骤"不写死固定集合,按 runOutcome 判断**:拒绝审批的 Run 不要求存在 FIX_EXECUTED;needs_human 的 Run 不要求 RECOVERY_VERIFIED。

## 4. Replay Projector(投影层)

- **组装层次**:按 `(logical_step_id, attempt_no)` 组装 Attempt → 再组合 Attempts 为一个 UI 步骤:

```json
{ "stepIndex": 3, "logicalStepId": "step_xxx", "sourceSequenceNos": [10, 11],
  "stepState": "completed", "stepOutcome": "succeeded" }
```

**三层状态语义(避免歧义)**:`phase`(DB 记录生命周期 started/completed/failed)→ `stepState`(Projector 聚合后的 UI 状态 completed/incomplete/started)→ `stepOutcome`(业务结果 succeeded/rejected/expired/needs_human/already_resolved/target_changed)。

- `stepIndex` = 投影后的 UI 步骤下标(非数据库 sequence_no);API 的 `{step_index}` 均指投影下标。
- **播放时长计算**:`actualDurationMs` 是历史事实(持久化);`displayDurationMs` 是播放策略(不落库),由 Projector 按 `step_type + actualDurationMs + playbackPolicyVersion` 计算;API 返回两者。
- **版本适配器**:按 `sourceReplaySchemaVersion` 读取旧快照,不默认所有历史快照结构相同。
- **partial 占位**:started 无 completed/failed → 输出 `stepStatus=incomplete` + `missingParts=["stateAfter","operationResult"]`,时间轴**显式缺失节点**,不跳过。
- **连续性校验**:前一步 `stateAfter` 与下一步 `stateBefore` 不一致时,标记缺失转换(防止投影掩盖状态断层)。
- 旧数据投影出的未知字段保持 `unknown / unavailable`,不能因审计表查不到就显示成 `refuted/false`。

## 5. Replay API(只读,按 Run 限定)

```
GET /api/incidents/{incident_id}/replay
    → Incident 级 Manifest:replay_status / run 列表 / defaultRunId / schema 版本
GET /api/incidents/{incident_id}/replay/runs/{agent_run_id}
    → 单 Run Manifest
GET /api/incidents/{incident_id}/replay/runs/{agent_run_id}/steps
    → 单 Run 可播放步骤(一次返回全部播放必需数据)
GET /api/incidents/{incident_id}/replay/runs/{agent_run_id}/steps/{logical_step_id}
    → 单步技术详情(懒加载,稳定 ID)
```

- **归属校验**:`agent_run.incident_id == URL 的 incident_id`,防替换 Run ID 跨 Incident 读。
- **defaultRunId**:优先最新已终止 Run(`terminated_at DESC, id DESC`);无已终止则最新 `in_progress` Run;完全没有 Run → `null`。
- Manifest 字段:

```json
{
  "replayId": "replay_xxx",
  "replayStatus": "complete|partial|partial_legacy|in_progress|unsupported|unavailable",
  "runStatus": "terminated|in_progress", "runOutcome": "...", "terminationReason": null,
  "asOfSequenceNo": 42,
  "sourceReplaySchemaVersion": "1.0", "responseSchemaVersion": "1.0", "playbackPolicyVersion": "1",
  "totalSteps": 18,
  "keyStepIndexes": { "diagnosis": 7, "approval": 9, "execution": 11, "recovery": 13 },
  "supportedSpeeds": [1, 2, 4]
}
```

  - `asOfSequenceNo`:保证读取 in_progress Run 时 Manifest 与 /steps 投影的是**同一时间截面**。
  - `keyStepIndexes` 引用 **stepIndex**;**重复节点选择规则**:diagnosis=第一次确认根因的诊断步骤;approval=审批决定步骤(非审批请求);execution=最终处置 Attempt;recovery=最终恢复验证步骤。关键节点不存在时不返回该字段(前端禁用对应跳转);或直接返回数组避免丢失重复诊断信息。
- **/steps 一次返回播放必需数据**:`stateBefore / stateAfter / decisionSummary / operationSummary / sourceReferenceSummary / actualDurationMs / displayDurationMs`——加载完成后断网核心播放仍继续。单步详情接口(稳定 `logical_step_id`)懒加载:脱敏参数详情 / MCP 调用信息 / PromQL 模板信息 / Trace 与审计引用 / 版本信息 / 较大规范化结果。
- **全部只读**:不触发状态机、不调 LLM/MCP、不执行审批处置、不重算 Policy;返回**后端脱敏**数据(不依赖前端隐藏敏感字段)。
- Run 可回放状态语义:`complete/partial/partial_legacy` 可播放;`in_progress` 可查看已有步骤但**默认禁用自动播放**(提示调查未结束);`unsupported` 禁止播放(Schema 不兼容);`unavailable` 显示"无回放数据"。

## 6. 前端回放页

### 6.1 入口与加载

详情页 → "查看历史回放" → 加载 Manifest 与 steps → **默认暂停在 position=0**(不自动播放,防错过开头)→ 用户点播放。

URL:`/replay?runId=123&position=8`;缺 `runId` 用 defaultRunId;`position` 越界修正到有效范围;Run 切换回 position=0 并暂停;浏览器前进/后退恢复 Run+position;**不把播放/暂停状态写入 URL**。

### 6.2 播放位置语义(状态位置,非步骤下标)

播放游标定义为**"状态位置"**:

```
position=0:尚未执行任何步骤,显示 steps[0].stateBefore
position=N:已执行前 N 个步骤,显示 steps[N-1].stateAfter
```

- 默认打开 position=0;播放一次后进入 position=1
- 上一步/下一步、刷新恢复、完成状态都有唯一含义
- 时间轴当前步骤 = position - 1(从 position 推导)

### 6.3 布局

- 顶部:步骤时间轴(实际节点序列;partial 缺失节点显式标记,不用动画/文字补全)
- 左侧:状态快照——
  - **假设状态:proposed / confirmed(与现有领域模型枚举一致;根因确认 `confirmed_hypothesis_id` 独立展示,不引入系统不存在的 supported/refuted 假设状态)**
  - 共享 Facts:unknown / supported / refuted / stale / conflict(证据支持度,快照层)
  - 双 DiagnosticPolicy + 安全排他条件(分区展示,不与根因证据混)+ 当前根因判定
- 右侧:本步详情——**执行前摘要 → 决策与操作 → 执行结果 → 状态变化**(见 6.5)
- 顶部常显:"历史回放 · 只读 · 不会执行任何系统操作"
- 默认摘要视图;"技术详情"折叠区:脱敏参数 / MCP 调用记录 / PromQL 模板 ID / Trace ID / 参数 Hash / Fact 判定规则版本 / 审计关联 ID(不默认展示完整工具原始输出、完整 Prompt、大段 JSON)

### 6.4 状态机与控制条

状态机:`IDLE → PLAYING ↔ PAUSED → COMPLETED`;任意状态 → `SEEKING → PAUSED`;错误 → `ERROR`。

- 控制条:播放/暂停、上一步/下一步、1×/2×/4×、拖动/点击任意步、跳转根因/审批/处置/恢复、重新播放
- **手动跳转后自动暂停**(便于讲解);页面不可见自动暂停;组件卸载清理 Timer;同一时间仅一个 Timer
- **单次 setTimeout**(每步 displayDurationMs 不同,不用 setInterval):进 PLAYING 按当前 Step+倍速创建 setTimeout → 到期下一步 → 再创建;暂停/跳转/切 Run/切速/隐藏/卸载/ERROR/COMPLETED 均先取消现有 Timer;切速可从切换时刻按新速度重计(V1.5 简化实现)
- 播放节奏:1×=displayDurationMs,2×=一半,4×=四分之一;界面显示真实耗时(`actualDurationMs`),播放用压缩时间
- 步骤切换效果:时间轴激活节点变化;左侧状态**高亮变化字段**;Fact 颜色平滑更新;右侧新步骤淡入;自动滚动到当前节点;避免复杂动画

### 6.5 选中位置状态语义

- position=0:显示第 0 步的 `stateBefore`
- position=N(N≥1):显示 `steps[N-1].stateAfter`,高亮标记 `stateBefore → stateAfter` 变化的字段
- 右侧依次:执行前状态摘要 → 决策与操作 → 执行结果 → 状态变化

### 6.6 颜色语义(颜色之外保留文字+图标)

```
unknown: 灰 / supported: 蓝 / confirmed: 绿 / refuted: 浅红或删除线 / stale: 橙 / conflict: 红
```

## 7. 测试策略

| 层 | 覆盖 |
|---|---|
| SnapshotFactory | 字段白名单、脱敏、确定性排序、Canonical JSON、Hash 稳定性 |
| Replay Writer | 并发序号分配、序号+插入同事务、重复审批幂等(logical_step_id 复用)、started 后崩溃 |
| Projector | 多 phase、多 Attempt 组装、partial、legacy、unsupported、引用丢失/Hash 校验、连续性校验(前步 stateAfter ≠ 后步 stateBefore → 标记缺失转换) |
| API | Run 归属校验、只读、无副作用、版本适配、asOfSequenceNo 同截面、关键节点选择规则 |
| Vue | Fake Timer、切速、隐藏暂停、卸载清理、浏览器前进/后退、Run 切换回 position=0 |
| E2E | SCN-001、SCN-002 均生成 complete Replay;另覆盖 rejected、needs_human、already_resolved、处置失败路径 |

## 8. 验收标准

### 交互

播放、暂停、上一步/下一步正常;1×/2×/4× 正确;可跳转任意步骤与关键节点(缺失节点按钮禁用);手动跳转后自动暂停;页面隐藏自动暂停;刷新后经 `?runId=&position=` 恢复;完成后可重新播放;同一页面无重复计时器;partial 回放显式展示缺失事件(incomplete 占位)。

### 只读无副作用(证明回放真无副作用)

- 播放期间**除静态资源与前端遥测外,业务 API 只允许 Replay 相关 GET**;进入回放页必须**关闭 Incident SSE 与详情页轮询**
- 无 Incident 创建、审批、执行、LLM 或 MCP 请求
- 播放前后 `Incident / Approval / FixExecution` 记录完全不变
- 重复打开同一 Run → 相同步骤顺序与快照 Hash
- 打开"技术详情"只产生 GET
- 恶意修改 `runId` 不能越过 Incident 归属校验
- 返回数据经后端脱敏,不依赖前端隐藏敏感字段

## 9. 范围外(后续)

- 回放导出(视频/PDF 复盘)
- 回放与真实观测数据联动(时间轴对齐 Prometheus/Jaeger 时间线)
- 多 Run 对比视图
- 回放速度的智能默认(按步骤类型自适应)
