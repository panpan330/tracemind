# V1.14 前端 Agent 进度面板(节点级渐进展示) 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 前端复用现有 SSE 事件流,新增"Agent 进度面板"——用户实时看到诊断走到哪个节点、当前进行到哪、是否降级。后端零改动。

**Architecture:** `useIncidentStream` 扩展出 `events`(收集全部事件,去重+排序,映射中文标签);`IncidentDetailView` 新增垂直时间线面板展示。纯前端,零新依赖。

**Tech Stack:** Vue 3 + Element Plus + vitest(沿用现有 FakeEventSource 测试模式)。

## Global Constraints

- 后端零改动(事件流已全量推送 `incident_event` 表,前端消费即得)。
- 零新增依赖(纯 Element Plus:el-timeline / el-tag)。
- 事件去重沿用 `seen` Set;按 sequence 排序。
- 未知事件类型 → 显示原始事件名(降级不崩)。
- 点击节点条目 → 跳 `/replay?incidentId=`(不精确定位 position)。
- 不做 LLM 流式 token、不做 ReplayView 精确定位、不做事件回放控制。
- 前端测试沿用现有模式:`useIncidentStream.test.ts` 的 FakeEventSource.dispatch、`IncidentDetailView.test.ts` 的 vi.stubGlobal('EventSource')。

## File Structure

- `web/src/api/types.ts`(Modify):加 `AgentEventItem` 类型。
- `web/src/composables/useIncidentStream.ts`(Modify):加 `events` + 标签映射。
- `web/src/composables/useIncidentStream.test.ts`(Modify):events 收集/去重/排序/标签测试。
- `web/src/views/IncidentDetailView.vue`(Modify):加 Agent 进度面板。
- `web/src/views/IncidentDetailView.test.ts`(Modify):面板渲染/终态/空态测试。

---

### Task 1:事件消费扩展(useIncidentStream.events + 标签映射)

**Files:**
- Modify: `web/src/api/types.ts`、`web/src/composables/useIncidentStream.ts`
- Test: `web/src/composables/useIncidentStream.test.ts`

**Interfaces:**
- Produces: `useIncidentStream()` 返回新增 `events: Ref<AgentEventItem[]>`;`AgentEventItem{sequence,type,label,status?,occurredAt}`;`EVENT_LABELS` 常量(类型→中文标签)。Task 2 的面板消费 events。

- [ ] **Step 1: 写失败测试**

```ts
// web/src/composables/useIncidentStream.test.ts(追加)
import type { AgentEventItem } from '@/api/types'

describe('useIncidentStream events', () => {
  it('收集全部事件并按 sequence 排序', async () => {
    const stream = useIncidentStream(3)
    FakeEventSource.dispatch(2, 'FIX_PROPOSED', JSON.stringify({ run_id: 5 }))
    FakeEventSource.dispatch(1, 'HYPOTHESES_GENERATED', JSON.stringify({ run_id: 5 }))
    await nextTick()
    expect(stream.events.value.length).toBe(2)
    expect(stream.events.value[0].sequence).toBe(1)
    expect(stream.events.value[1].sequence).toBe(2)
    stream.close()
  })

  it('映射中文标签,未知类型显示原始名', async () => {
    const stream = useIncidentStream(3)
    FakeEventSource.dispatch(1, 'FIX_PROPOSED', JSON.stringify({ run_id: 5 }))
    FakeEventSource.dispatch(2, 'UNKNOWN_NODE', JSON.stringify({}))
    await nextTick()
    expect(stream.events.value[0].label).toBe('提出修复')
    expect(stream.events.value[1].label).toBe('UNKNOWN_NODE')
    stream.close()
  })

  it('status_changed 记录 status,不重复显示为节点', async () => {
    const stream = useIncidentStream(3)
    FakeEventSource.dispatch(1, 'HYPOTHESES_GENERATED', JSON.stringify({ run_id: 5 }))
    FakeEventSource.dispatch(2, 'status_changed', JSON.stringify({ status: 'investigating' }))
    await nextTick()
    // events 含全部(节点 + status_changed),status_changed 带 status
    const sc = stream.events.value.find(e => e.type === 'status_changed')
    expect(sc?.status).toBe('investigating')
    stream.close()
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd web && npx vitest run src/composables/useIncidentStream.test.ts`
Expected: FAIL(`stream.events` undefined 或 `.length` 报错)

- [ ] **Step 3: 实现**

`types.ts` 加:

```ts
export interface AgentEventItem {
  sequence: number
  type: string
  label: string
  status?: string
  occurredAt: string
}
```

`useIncidentStream.ts`:

```ts
import type { AgentEventItem, IncidentStatus } from '@/api/types'

export interface IncidentStream {
  status: Ref<IncidentStatus | null>
  events: Ref<AgentEventItem[]>
  lastEventId: Ref<number>
  connected: Ref<boolean>
  close: () => void
}

export const EVENT_LABELS: Record<string, string> = {
  INCIDENT_INGESTED: '事件受理',
  HYPOTHESES_GENERATED: '生成假设',
  EVIDENCE_COLLECTION: '证据采集',
  DIAGNOSIS_EVALUATED: '根因评估',
  FIX_PROPOSED: '提出修复',
  APPROVAL_REQUESTED: '等待审批',
  FIX_EXECUTED: '执行修复',
  RECOVERY_VERIFIED: '恢复验证',
  REPORT_GENERATED: '生成报告',
  REFLECTION_EVALUATED: '反思复盘',
  llm_degraded: '能力降级',
  rag_degraded: '能力降级',
}

export function labelFor(type: string): string {
  return EVENT_LABELS[type] ?? type
}
```

`useIncidentStream` 内部:

```ts
  const events = ref<AgentEventItem[]>([])
  // handle() 内,在解析 data 后:
    events.value = [...events.value, {
      sequence: seq,
      type: event,
      label: labelFor(event),
      status: (data.status as string | undefined),
      occurredAt: new Date().toISOString(),
    }].sort((a, b) => a.sequence - b.sequence)
```

(注:去重沿用现有 `seen`;事件收集放在 `status_changed`/`incident_finished` 分支之前,所有事件都收。)

- [ ] **Step 4: 运行确认通过**

Run: `cd web && npx vitest run src/composables/useIncidentStream.test.ts`
Expected: PASS(既有 2 + 新 3)

- [ ] **Step 5: 提交**

```bash
git add web/src/api/types.ts web/src/composables/useIncidentStream.ts web/src/composables/useIncidentStream.test.ts
git commit -m "feat(stream): useIncidentStream 收集全部事件 + 中文标签映射"
```

---

### Task 2:Agent 进度面板 UI

**Files:**
- Modify: `web/src/views/IncidentDetailView.vue`
- Test: `web/src/views/IncidentDetailView.test.ts`

**Interfaces:**
- Consumes: `useIncidentStream().events`(Task 1)。
- Produces: IncidentDetailView 状态卡片下方新增 `data-testid="agent-progress"` 垂直时间线面板。无新接口。

- [ ] **Step 1: 写失败测试**

```ts
// web/src/views/IncidentDetailView.test.ts(追加)
describe('Agent 进度面板', () => {
  it('渲染节点事件时间线', async () => {
    // mock useIncidentStream 返回 events(见下;若组件内直接调 useIncidentStream,
    // 则通过 stubGlobal EventSource + FakeEventSource.dispatch 驱动,与现有测试一致)
    ...
  })
  it('未知事件类型显示原始名不崩', async () => { ... })
  it('recovered 终态显示完成', async () => { ... })
  it('无事件显示等待文案', async () => { ... })
})
```

(实现时若 IncidentDetailView 直接从 useIncidentStream 解构 events,测试用现有 `vi.stubGlobal('EventSource', ...)` 驱动——先读现有测试的 stub 方式,对齐。)

- [ ] **Step 2: 运行确认失败**

Run: `cd web && npx vitest run src/views/IncidentDetailView.test.ts`
Expected: FAIL(面板不存在,`agent-progress` 找不到)

- [ ] **Step 3: 实现**

`IncidentDetailView.vue` 模板,状态卡片后加:

```html
    <el-card shadow="never" class="agent-progress-card" data-testid="agent-progress">
      <template #header>Agent 进度</template>
      <el-timeline v-if="events.length">
        <el-timeline-item
          v-for="(ev, idx) in events"
          :key="ev.sequence"
          :type="timelineType(ev, idx)"
          :timestamp="ev.occurredAt ? new Date(ev.occurredAt).toLocaleTimeString() : ''"
        >
          <span :data-testid="`agent-event-${ev.sequence}`">{{ ev.label }}</span>
          <el-tag v-if="ev.type === 'llm_degraded' || ev.type === 'rag_degraded'"
                  type="warning" size="small" style="margin-left: 8px">降级</el-tag>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-else description="等待 Agent 启动…" />
    </el-card>
```

script 加:

```ts
import { useIncidentStream } from '@/composables/useIncidentStream'
// 现有 const { status: liveStatus } = useIncidentStream(incidentId) 改为:
const { status: liveStatus, events } = useIncidentStream(incidentId)

function timelineType(ev: { type: string; status?: string }, idx: number) {
  if (ev.type === 'llm_degraded' || ev.type === 'rag_degraded') return 'warning'
  if (ev.type === 'incident_finished') {
    return ev.status === 'recovered' ? 'success' : 'danger'
  }
  if (idx === events.value.length - 1 && !isTerminal(detail.value?.status)) return 'primary'
  return ''
}
```

(若 `events` 与 `detail` 命名冲突,按实际组件内现有变量调整。)

- [ ] **Step 4: 运行确认通过**

Run: `cd web && npx vitest run src/views/IncidentDetailView.test.ts`
Expected: PASS(既有 + 新 4)

- [ ] **Step 5: 提交**

```bash
git add web/src/views/IncidentDetailView.vue web/src/views/IncidentDetailView.test.ts
git commit -m "feat(ui): Agent 进度面板 — 节点级时间线(进行中/终态/降级)"
```

---

### Task 3:整体回归 + build

**Files:**
- 全部改动文件。

**Interfaces:**
- 无新接口;验证 Task 1-2 集成。

- [ ] **Step 1: 前端全量测试**

Run: `cd web && npx vitest run`
Expected: 全部 PASS(原 38 + 新增,无回归)。

- [ ] **Step 2: 类型检查 + build**

```bash
cd web && npx vue-tsc --noEmit && npx vite build
```

Expected: 无错 + build 成功。

- [ ] **Step 3: 后端全量(确认零改动无意外)**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/ -q`
Expected: 全部 PASS(后端未动,应无变化)。

- [ ] **Step 4: 提交(如有修复)+ 推送**

```bash
git add -A && git commit -m "fix(progress): 回归修复"
git push origin main
```

(注意:GitHub 网络间歇不可用——若失败记录待推提交数,稍后重试。)
