# V1.14 设计:前端 Agent 进度面板(节点级渐进展示)

日期:2026-08-14
版本:V1.14
前置:V1.13(评测平台可视化)

## 1. 背景与目标

系统已有 SSE 事件基建:`app/api/stream.py` 全量推送节点事件,前端 `useIncidentStream` 消费。但**前端只消费 status_changed / incident_finished**,节点级事件(HYPOTHESES_GENERATED / FIX_PROPOSED / REPORT_GENERATED 等)到了前端被丢弃——用户看不到 Agent"正在走到哪一步"。对照"AI Agent / 大模型应用工程师"岗位,缺一块**Agent 可解释性/透明性**的展示能力。

本版本目标:**只在前端做节点级渐进展示**——复用现有 SSE 事件流,新增"Agent 进度面板",用户实时看到诊断走到哪个节点、当前进行到哪、是否降级。后端零改动(事件流已全量推送)。

## 2. 现状盘点

- **SSE 基建**:`app/api/stream.py` 事件流(快照 + Last-Event-ID + 轮询 + heartbeat + 终态关闭),全量推送 `incident_event` 表事件。
- **事件结构**:`IncidentEvent{incident_id, sequence, event_type, payload, occurred_at}`(db/models.py:203)。
- **事件类型**(nodes.py 已产出):`status_changed`(payload 带 status)、节点事件(HYPOTHESES_GENERATED / EVIDENCE_COLLECTION / DIAGNOSIS_EVALUATED / FIX_PROPOSED / APPROVAL_REQUESTED / FIX_EXECUTED / RECOVERY_VERIFIED / REPORT_GENERATED / REFLECTION_EVALUATED / INCIDENT_INGESTED 等)、降级事件(`llm_degraded` / `rag_degraded`)、`incident_finished`。
- **前端**:`useIncidentStream(incidentId)` 返回 `{status, lastEventId, connected, close}`,内部有 `seen` Set 去重、按 sequence 记 lastEventId;IncidentDetailView 用它 + 3s 轮询兜底。
- **约束**:零新增依赖,纯 Element Plus。

## 3. 段 1:事件流前端消费扩展

### 3.1 useIncidentStream 增强

- 新增 `events: Ref<AgentEventItem[]>`(当前只有 status)。
- 所有事件都收集进 `events`(不只 status_changed),每条:`{sequence, type, label, status?, occurredAt}`。
- 去重沿用现有 `seen` Set;按 sequence 排序。
- `incident_finished` 后仍保留已有 events(面板定格)。

### 3.2 新增类型(types.ts)

```ts
export interface AgentEventItem {
  sequence: number
  type: string          // 原始事件类型
  label: string         // 可读中文标签
  status?: string       // status_changed 事件带
  occurredAt: string
}
```

### 3.3 事件类型 → 标签映射(前端常量)

| 事件类型 | 标签 |
|---|---|
| INCIDENT_INGESTED | 事件受理 |
| HYPOTHESES_GENERATED | 生成假设 |
| EVIDENCE_COLLECTION | 证据采集 |
| DIAGNOSIS_EVALUATED | 根因评估 |
| FIX_PROPOSED | 提出修复 |
| APPROVAL_REQUESTED | 等待审批 |
| FIX_EXECUTED | 执行修复 |
| RECOVERY_VERIFIED | 恢复验证 |
| REPORT_GENERATED | 生成报告 |
| REFLECTION_EVALUATED | 反思复盘 |
| llm_degraded / rag_degraded | 能力降级(提示) |
| status_changed | 状态更新(不单独显示,驱动进度态) |
| incident_finished | 诊断结束 |

未知类型 → 显示原始事件名(降级不崩)。

## 4. 段 2:Agent 进度面板 UI

### 4.1 位置与形态

- IncidentDetailView 状态卡片下方新增区块,`data-testid="agent-progress"`。
- 垂直时间线(el-timeline):每个事件一条(图标 + 中文标签 + 时间)。
  - 进行中:最近一个未到终态的事件高亮(`type=primary`)。
  - 终态(recovered):最后一条完成色;needs_human:警示色。
  - 降级事件:warning 色标签。
- 空态:无事件时显示"等待 Agent 启动…"。

### 4.2 交互

- 点击节点条目 → 跳 `/replay?incidentId=`(不精确定位 position,列为轻量交互)。

### 4.3 测试

- mock `useIncidentStream` 返回 events → 面板渲染时间线条目。
- 事件映射:未知类型显示原始名(不崩)。
- 终态样式:recovered 显示完成态。
- 空态文案。

## 5. 测试与验收

### 5.1 前端测试

- `IncidentDetailView.test.ts` 扩展或新增 `AgentProgressPanel.test.ts`:
  1. 渲染 events 时间线条目(节点事件 + 中文标签)
  2. 未知事件类型显示原始名(降级不崩)
  3. 终态 recovered 完成态样式
  4. 空态"等待 Agent 启动…"
- `useIncidentStream` 若有单测文件则扩展(events 收集/去重/排序)。

### 5.2 回归

- 前端全量 vitest(当前 38 passed,无回归)。
- 后端不动(零改动,跑全量 pytest 确认无意外影响——理论无影响)。

### 5.3 验收

- 前端 build + 单测通过。
- 本地/VM 可选:浏览器打开 IncidentDetail,观察诊断过程事件逐条出现(不强制 VM,前端本地 build 验证即可)。

## 6. 范围边界(明确不做)

- 不做 LLM 流式 token 输出(不碰 LLM streaming API,用户已选前端方案)。
- 不做 ReplayView 精确定位跳转(position 映射留后续)。
- 不做事件暂停/回放控制。
- 不引入新依赖、后端零改动。

## 7. 简历亮点(面试可讲)

1. **Agent 可解释性/透明性**:用户实时看到诊断走到哪个节点、当前进行到哪、是否降级——"Agent 不是黑盒,过程全链路可视化",LLM 应用产品化的高频面试点
2. **复用现有基建零改动后端**:SSE 事件流早已全量推送,前端消费即得——体现"最小增量"的工程判断
3. **稳健降级**:未知事件类型显示原始名不崩、断线轮询兜底、终态面板定格
