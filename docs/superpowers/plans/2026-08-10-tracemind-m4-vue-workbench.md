# M4 Vue 工作台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 V1.0 的 Vue 3 工作台(三个页面 + SSE 实时调查过程 + 审批面板),并用两个后端补丁补齐"恢复判定真实基线"与"状态事件流"两个 M3 遗留缺口。

**Architecture:** 前端 `web/`(Vue 3 + TypeScript + Vite + Element Plus + vue-router),开发期 Vite Proxy 将 `/api` 转发到 FastAPI(localhost:8000),EventSource 使用同源地址。前端只消费 AI 服务 API,不直连 Java/MySQL。两个后端补丁在 `ai-service/` 内 TDD 完成:① 健康指标基线采集与基于真实基线的恢复判定;② 状态变化事件写入与 SSE 终态关闭。

**Tech Stack:** Vue 3.5、TypeScript 5.7、Vite 6、Element Plus 2.9、vue-router 4、Vitest 2 + @vue/test-utils + jsdom;后端沿用 FastAPI + SQLAlchemy(现有)。

## Global Constraints

- V1.0 只有三个页面:场景与事件列表、Incident 调查详情、复盘报告。**不做**用户管理、聊天窗口、可拖拽工作流、监控大屏。
- 前端不得包含任何密码/API Key;demo 密钥只存在于 AI 服务环境变量。
- 前端 API 字段名**精确匹配** `ai-service/app/api/*.py` 现有响应(见各任务 Interfaces)。
- SSE 约定(设计文档 §9):快照先行、`Last-Event-ID` 断线补发、前端按 `event.id` 去重、15~30s heartbeat、Incident 进入终态后发送最终事件并关闭连接。
- 状态枚举(设计文档 §4.6):`created`、`investigating`、`awaiting_approval`、`executing`、`verifying`、`recovered`、`needs_human`、`rejected`、`failed`。终态集合:{`recovered`, `needs_human`, `rejected`, `failed`}。
- 恢复判定必须基于 Incident 记录的真实健康基线(相对阈值),**禁止硬编码毫秒数**。
- 开发地址:AI 服务 localhost:8000;Vue dev 5173;Java 8081/8082(仅 demo 代理经 AI 服务,前端不直连)。
- npm registry 已是华为云镜像(不需要再配);Windows 环境,shell 为 bash。
- 每个任务独立 commit;先写失败测试再实现(TDD)。

---

### Task 4.1: 前端脚手架(Vite + Vue3 + TS + Element Plus + 路由 + Proxy)

**Files:**
- Create: `web/package.json`
- Create: `web/vite.config.ts`
- Create: `web/tsconfig.json`、`web/tsconfig.node.json`
- Create: `web/index.html`
- Create: `web/src/main.ts`、`web/src/App.vue`
- Create: `web/src/router/index.ts`
- Create: `web/.gitignore`

**Interfaces:**
- Produces: `npm run dev`(dev server :5173,`/api` 代理到 `http://localhost:8000`)、`npm run build`(产物 `web/dist`)、`npm test`(Vitest + jsdom)。
- Produces: 路由表 `/` → `ScenarioView`(占位)、`/incidents/:id` → `IncidentDetailView`(占位)、`/incidents/:id/report` → `ReportView`(占位)。

- [ ] **Step 1: 写 package.json**

```json
{
  "name": "tracemind-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vue-tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "@element-plus/icons-vue": "^2.3.1",
    "element-plus": "^2.9.1",
    "vue": "^3.5.13",
    "vue-router": "^4.5.0"
  },
  "devDependencies": {
    "@types/node": "^22.10.2",
    "@vitejs/plugin-vue": "^5.2.1",
    "@vue/test-utils": "^2.4.6",
    "jsdom": "^25.0.1",
    "typescript": "~5.7.2",
    "vite": "^6.0.5",
    "vitest": "^2.1.8",
    "vue-tsc": "^2.2.0"
  }
}
```

- [ ] **Step 2: 写 vite.config.ts(proxy + vitest 配置)**

```ts
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.test.ts'],
  },
})
```

- [ ] **Step 3: 写 tsconfig.json / tsconfig.node.json / index.html**

`tsconfig.json`(工程引用 app + node):

```json
{
  "files": [],
  "references": [
    { "path": "./tsconfig.app.json" },
    { "path": "./tsconfig.node.json" }
  ]
}
```

`tsconfig.app.json`(编译器配置 + `src` 覆盖):

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "module": "ESNext",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "verbatimModuleSyntax": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "preserve",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "baseUrl": ".",
    "paths": { "@/*": ["./src/*"] },
    "types": ["vitest/globals"]
  },
  "include": ["src/**/*.ts", "src/**/*.vue"]
}
```

`tsconfig.node.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2023"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "verbatimModuleSyntax": true,
    "moduleDetection": "force",
    "noEmit": true,
    "strict": true,
    "types": ["node"]
  },
  "include": ["vite.config.ts"]
}
```

`index.html`:

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>TraceMind 工作台</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

- [ ] **Step 4: 写 main.ts / App.vue / router**

`web/src/main.ts`:

```ts
import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import router from './router'

createApp(App).use(ElementPlus).use(router).mount('#app')
```

`web/src/App.vue`:

```vue
<template>
  <el-container class="app-shell">
    <el-header class="app-header">
      <router-link to="/" class="brand">TraceMind 工作台</router-link>
      <span class="subtitle">AI 故障诊断与安全处置平台</span>
    </el-header>
    <el-main><router-view /></el-main>
  </el-container>
</template>

<style scoped>
.app-shell { min-height: 100vh; }
.app-header { display: flex; align-items: baseline; gap: 12px; border-bottom: 1px solid var(--el-border-color); }
.brand { font-size: 18px; font-weight: 600; text-decoration: none; color: var(--el-color-primary); }
.subtitle { color: var(--el-text-color-secondary); font-size: 13px; }
</style>
```

`web/src/router/index.ts`:

```ts
import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'scenario', component: () => import('@/views/ScenarioView.vue') },
    { path: '/incidents/:id', name: 'incident-detail', component: () => import('@/views/IncidentDetailView.vue') },
    { path: '/incidents/:id/report', name: 'incident-report', component: () => import('@/views/ReportView.vue') },
  ],
})

export default router
```

- [ ] **Step 5: 写三个视图占位文件**

`web/src/views/ScenarioView.vue`、`IncidentDetailView.vue`、`ReportView.vue` 各自只含:

```vue
<template><div>TODO 占位(下一任务替换)</div></template>
```

(此占位仅用于让路由可编译,后续任务立即替换为真实页面,不算计划占位符。)

- [ ] **Step 6: 写 .gitignore 并安装依赖**

`web/.gitignore`:

```
node_modules/
dist/
```

Run: `cd web && npm install`
Expected: 安装成功(华为云镜像)。

- [ ] **Step 7: 验证脚手架**

Run: `cd web && npm run build`
Expected: BUILD SUCCESS(无 TS 错误)。
Run: `cd web && npm run dev`(后台),然后 `curl -s http://localhost:5173/ | head -5`
Expected: 返回含 `<div id="app">` 的 HTML。
Run: `curl -s http://localhost:5173/api/health`
Expected: 转发到 AI 服务返回 `{"status":"ok"}`(需 AI 服务已启动;若未启动为 502 也说明代理生效,可接受)。

- [ ] **Step 8: Commit**

```bash
git add web/
git commit -m "feat(web): Vue3+TS+Vite+Element Plus 脚手架与路由骨架"
```

---

### Task 4.2: API 客户端与类型定义 + 状态工具

**Files:**
- Create: `web/src/api/types.ts`
- Create: `web/src/api/client.ts`
- Create: `web/src/utils/status.ts`
- Test: `web/src/utils/status.test.ts`

**Interfaces:**
- Consumes: 现有 AI 服务 API(字段名见下方类型定义,与 `ai-service/app/api/*.py` 响应一致)。
- Produces: `types.ts` 导出 `IncidentListItem`、`IncidentDetail`、`Hypothesis`、`EvidenceItem`、`Approval`、`FixExecution`、`RecoveryCheck`、`ScenarioStatus`、`IncidentStatus`(联合类型)。
- Produces: `client.ts` 导出 `listIncidents()`、`getIncident(id)`、`createIncident(input)`、`startInvestigation(id)`、`getRun(id, runId)`、`decideApproval(incidentId, approvalId, decision, comment)`、`injectScenario()`、`resetScenario()`、`getScenarioStatus()`。
- Produces: `status.ts` 导出 `STATUS_META: Record<IncidentStatus, { label: string; tag: 'success'|'warning'|'danger'|'info'|'primary' }>` 与 `isTerminal(status): boolean`。

- [ ] **Step 1: 写失败测试**

`web/src/utils/status.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { STATUS_META, isTerminal } from './status'

describe('status 元数据', () => {
  it('覆盖全部 9 个状态', () => {
    expect(Object.keys(STATUS_META).sort()).toEqual(
      ['awaiting_approval', 'created', 'executing', 'failed', 'investigating', 'needs_human', 'recovered', 'rejected', 'verifying'].sort(),
    )
  })
  it('终态判定', () => {
    expect(isTerminal('recovered')).toBe(true)
    expect(isTerminal('needs_human')).toBe(true)
    expect(isTerminal('rejected')).toBe(true)
    expect(isTerminal('failed')).toBe(true)
    expect(isTerminal('awaiting_approval')).toBe(false)
    expect(isTerminal('created')).toBe(false)
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd web && npx vitest run src/utils/status.test.ts`
Expected: FAIL(`Cannot find module './status'`)。

- [ ] **Step 3: 写 types.ts**

`web/src/api/types.ts`:

```ts
export type IncidentStatus =
  | 'created' | 'investigating' | 'awaiting_approval' | 'executing' | 'verifying'
  | 'recovered' | 'needs_human' | 'rejected' | 'failed'

export interface IncidentListItem {
  id: number
  title: string
  status: IncidentStatus
  severity: string
  created_at: string
}

export interface Hypothesis {
  id: number
  description: string
  status: string
}

export interface EvidenceItem {
  id: string
  source: string
  key: string | null
  passed: boolean | null
  content: unknown
}

export interface Approval {
  id: number
  fix_proposal_id: number
  status: string
  approver: string | null
  comment: string | null
  expires_at: string | null
}

export interface FixExecution {
  id: number
  fix_proposal_id: number
  status: string
}

export interface RecoveryCheck {
  id: number
  status: string
  index_present: boolean | null
  query_plan_uses_target_index: boolean | null
  estimated_rows_after: number | null
}

export interface IncidentDetail {
  id: number
  title: string
  status: IncidentStatus
  severity: string
  service_ref: string
  created_at: string
  finished_at: string | null
  hypotheses: Hypothesis[]
  evidence: EvidenceItem[]
  approvals: Approval[]
  fix_execution: FixExecution | null
  recovery: RecoveryCheck | null
  report: Record<string, unknown> | null
}

export interface ScenarioStatus {
  indexPresent: boolean
}

export interface CreateIncidentInput {
  title: string
  description?: string
  severity: string
  service_ref: string
  observed_at?: string
}
```

- [ ] **Step 4: 写 status.ts**

`web/src/utils/status.ts`:

```ts
import type { IncidentStatus } from '@/api/types'

export const STATUS_META: Record<IncidentStatus, { label: string; tag: 'success' | 'warning' | 'danger' | 'info' | 'primary' }> = {
  created: { label: '已创建', tag: 'info' },
  investigating: { label: '调查中', tag: 'primary' },
  awaiting_approval: { label: '待审批', tag: 'warning' },
  executing: { label: '执行中', tag: 'primary' },
  verifying: { label: '验证中', tag: 'primary' },
  recovered: { label: '已恢复', tag: 'success' },
  needs_human: { label: '需人工介入', tag: 'danger' },
  rejected: { label: '已拒绝', tag: 'info' },
  failed: { label: '失败', tag: 'danger' },
}

const TERMINAL: ReadonlySet<IncidentStatus> = new Set(['recovered', 'needs_human', 'rejected', 'failed'])

export function isTerminal(status: IncidentStatus): boolean {
  return TERMINAL.has(status)
}
```

- [ ] **Step 5: 写 client.ts**

`web/src/api/client.ts`:

```ts
import type { CreateIncidentInput, IncidentDetail, IncidentListItem, ScenarioStatus } from './types'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!resp.ok) {
    const text = await resp.text().catch(() => '')
    throw new Error(`API ${resp.status}: ${text.slice(0, 200)}`)
  }
  return resp.json() as Promise<T>
}

export function listIncidents(): Promise<IncidentListItem[]> {
  return request('/api/incidents')
}

export function getIncident(id: number): Promise<IncidentDetail> {
  return request(`/api/incidents/${id}`)
}

export function createIncident(input: CreateIncidentInput): Promise<{ id: number; status: string; title: string; service_ref: string }> {
  return request('/api/incidents', { method: 'POST', body: JSON.stringify(input) })
}

export function startInvestigation(id: number): Promise<{ run_id: number; thread_id: string; status: string }> {
  return request(`/api/incidents/${id}/investigations`, { method: 'POST' })
}

export function getRun(id: number, runId: number): Promise<{ run_id: number; status: string; investigation_round: number; tool_call_count: number }> {
  return request(`/api/incidents/${id}/runs/${runId}`)
}

export function decideApproval(incidentId: number, approvalId: number, decision: 'approved' | 'rejected', comment: string): Promise<unknown> {
  return request(`/api/incidents/${incidentId}/approvals/${approvalId}/decision`, {
    method: 'POST',
    body: JSON.stringify({ decision, comment }),
  })
}

export function injectScenario(): Promise<unknown> {
  return request('/api/demo/scenarios/SCN-001/inject', { method: 'POST' })
}

export function resetScenario(): Promise<unknown> {
  return request('/api/demo/scenarios/SCN-001/reset', { method: 'POST' })
}

export function getScenarioStatus(): Promise<ScenarioStatus> {
  return request('/api/demo/scenarios/SCN-001/status')
}
```

- [ ] **Step 6: 运行测试确认通过**

Run: `cd web && npx vitest run src/utils/status.test.ts`
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add web/src/api web/src/utils web/src/views
git commit -m "feat(web): API 客户端、类型定义与状态元数据工具"
```

---

### Task 4.3: 后端补丁 A — 健康指标基线采集 + 真实基线恢复判定

**背景:** 设计文档 §4.4/§6.2 要求恢复判定基于 Incident 记录的真实健康基线,但现状是 `nodes.py` 硬编码 `HEALTHY_BASELINE_P95 = 10`,且 `incident.healthy_metrics_baseline` 列被误存了 digest 基线。本任务修正语义并让 E1/恢复判定使用真实基线。

**Files:**
- Modify: `ai-service/app/repositories/incident_repo.py`(`save_incident_baseline` 语义改为健康指标基线)
- Modify: `ai-service/app/api/incidents.py`(create_incident 采集健康基线;digest 基线改存 agent_run 创建时)
- Modify: `ai-service/app/api/runs.py`(start_investigation 创建 run 时传 digest 基线)
- Modify: `ai-service/app/services/health_baseline_service.py`(新建:采集健康指标基线)
- Modify: `ai-service/app/services/recovery_service.py`(相对基线 P95 判定 + 三批固定探测)
- Modify: `ai-service/app/agent/nodes.py`(E1 判定与恢复验证使用 Incident 真实基线)
- Test: `ai-service/tests/test_health_baseline.py`(新建)

**Interfaces:**
- Consumes: Java 内部观测端点 `GET {inventory_service_url}/internal/observations/metrics?window_seconds=300`(返回 `{service, window_seconds, p95_ms, qps, error_rate, representative_slow_trace_id}`);`app.config.settings.inventory_service_url`;`incident_repo.save_health_baseline(incident_id, baseline)`。
- Produces: `health_baseline_service.capture_health_baseline(service_ref: str) -> dict | None`(httpx 调用 Java,超时 5s;失败/Java 未启动返回 `None`;返回 `{"p95_ms": int, "qps": float, "error_rate": float | None}`)。
- Produces: `recovery_service.verify_recovery(...)` 判定新增:p95 相对基线恢复(修复后固定探测 P95 ≤ 基线 × 1.2,连续 3 批;基线缺失时该条件视为通过)。
- Produces: `incident.healthy_metrics_baseline` 列只存健康指标基线;digest 基线存 `agent_run.incident_digest_baseline`。

- [ ] **Step 1: 先确认现状(读取)** — 执行 `grep -n "save_incident_baseline\|healthy_metrics_baseline\|incident_digest_baseline" ai-service/app -r` 与 `grep -n "baseline" ai-service/app/services/slow_query_service.py`,记录 digest 基线当前读取路径,确保补丁后 `list_expensive_query_digests` 仍能拿到 digest 增量基线。

- [ ] **Step 2: 写失败测试**

`ai-service/tests/test_health_baseline.py`:

```python
"""健康指标基线采集与真实基线恢复判定。"""
from unittest.mock import patch

import pytest

from app.services import recovery_service
from app.services.health_baseline_service import capture_health_baseline


class FakeMetricsResponse:
    status_code = 200

    def json(self):
        return {"service": "inventory-service", "window_seconds": 300,
                "p95_ms": 2, "qps": 20.0, "error_rate": 0.0}


class FakeMetricsResponseNull:
    status_code = 200

    def json(self):
        return {"service": "inventory-service", "window_seconds": 300,
                "p95_ms": None, "qps": 20.0, "error_rate": None}


def test_capture_health_baseline_ok():
    with patch("app.services.health_baseline_service.httpx.get",
               return_value=FakeMetricsResponse()) as m:
        baseline = capture_health_baseline("inventory-service")
    assert baseline == {"p95_ms": 2, "qps": 20.0, "error_rate": 0.0}
    m.assert_called_once()


def test_capture_health_baseline_unavailable_returns_none():
    with patch("app.services.health_baseline_service.httpx.get",
               side_effect=Exception("connection refused")):
        assert capture_health_baseline("inventory-service") is None


def test_capture_health_baseline_null_p95_returns_none():
    with patch("app.services.health_baseline_service.httpx.get",
               return_value=FakeMetricsResponseNull()):
        assert capture_health_baseline("inventory-service") is None


@pytest.mark.parametrize("p95_after,baseline,expected", [
    (2, {"p95_ms": 2}, True),      # 等于基线 -> 恢复
    (3, {"p95_ms": 2}, False),     # 3 > 2.4(2×1.2)-> 未恢复
    (2, None, True),               # 基线缺失 -> 视为通过
])
def test_p95_recovery_rule(p95_after, baseline, expected):
    from app.services.recovery_service import _p95_recovered
    assert _p95_recovered(p95_after, baseline) is expected
```

注意:上表第二行预期 `(3, {"p95_ms": 2})` 应为 `False`(3 > 2×1.2=2.4)。请在写测试时以注释为准修正参数表。

- [ ] **Step 3: 运行测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_health_baseline.py -q`
Expected: FAIL(模块不存在)。

- [ ] **Step 4: 实现 health_baseline_service.py**

`ai-service/app/services/health_baseline_service.py`:

```python
"""健康指标基线采集:Incident 创建时从 Java 观测端点取修复前健康 P95。"""
import httpx

from app.config import settings


def capture_health_baseline(service_ref: str) -> dict | None:
    """调用 Java 内部观测端点。Java 未启动/异常/P95 缺失时返回 None(调用方容错)。"""
    url = f"{settings.inventory_service_url}/internal/observations/metrics?window_seconds=300"
    try:
        resp = httpx.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    p95 = data.get("p95_ms")
    if p95 is None:
        return None
    return {"p95_ms": int(p95), "qps": data.get("qps"), "error_rate": data.get("error_rate")}
```

- [ ] **Step 5: 修正 incident_repo 与 API 的基线写入路径**

`incident_repo.py`:`save_incident_baseline` 改名为 `save_health_baseline`,签名改为 `save_health_baseline(incident_id: int, baseline: dict | None) -> None`(允许 `None`,健康基线采集失败时写 NULL)。仍写 `incident.healthy_metrics_baseline` 列。

`app/api/incidents.py` 的 `create_incident` 改为:

```python
@router.post("", status_code=201)
def create_incident(payload: IncidentIn):
    inc = incident_repo.create_incident(
        payload.title, payload.description, payload.severity, payload.service_ref)
    health = capture_health_baseline(payload.service_ref)
    incident_repo.save_health_baseline(inc.id, health)  # digest 基线由 start_investigation 创建 run 时采集
    return {"id": inc.id, "status": inc.status, "title": inc.title,
            "service_ref": inc.service_ref}
```

`app/api/runs.py` 的 `start_investigation` 改为在创建 run 时采集 digest 基线:

```python
@router.post("/{incident_id}/investigations", status_code=202)
async def start_investigation(incident_id: int):
    inc = incident_repo.get_incident(incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    baseline = capture_digest_baseline(get_readonly_engine())  # 开始调查时的新鲜基线
    run = run_repo.create_run(incident_id, baseline=baseline)
    from app.services import runner
    await runner.start_investigation(incident_id, run.id, run.thread_id)
    return {"run_id": run.id, "thread_id": run.thread_id, "status": "investigating"}
```

(删除原来从 `runs[0]` 取基线的逻辑;删除后 `capture_digest_baseline` 的 import 从 incidents.py 移到 runs.py。)

- [ ] **Step 6: recovery_service 增加相对基线 P95 判定**

在 `recovery_service.py` 增加:

```python
P95_RECOVERY_RATIO = 1.2
PROBE_BATCHES = 3
PROBE_PARAMS = {"skuId": 42, "warehouseId": 7}


def _p95_recovered(p95_after_ms: int | None, baseline: dict | None) -> bool:
    """修复后固定探测 P95 相对健康基线恢复(基线缺失视为通过)。"""
    if baseline is None or p95_after_ms is None:
        return True
    base = baseline.get("p95_ms")
    if not base:
        return True
    return p95_after_ms <= int(base) * P95_RECOVERY_RATIO


def _probe_p95_ms(readonly_engine, params: dict) -> int | None:
    """执行一批固定探测请求,返回本批最大耗时(ms)。"""
    import time
    start = time.monotonic()
    with readonly_engine.connect() as conn:
        conn.execute(text(
            "SELECT id FROM inventory WHERE sku_id = :s AND warehouse_id = :w"
        ), {"s": params["skuId"], "w": params["warehouseId"]})
    return int((time.monotonic() - start) * 1000)
```

在 `verify_recovery` 的 `recovered` 计算中追加:健康基线存在时执行 `PROBE_BATCHES` 批 `_probe_p95_ms`,全部满足 `_p95_recovered` 才为 recovered;并写入 `RecoveryCheck.latency_p95_after`(取三批中最大)与 `status`。恢复判定最终为:

```python
p95_after = max((_probe_p95_ms(get_readonly_engine(), PROBE_PARAMS) or 0) for _ in range(PROBE_BATCHES))
p95_ok = _p95_recovered(p95_after, baseline)
recovered = bool(index_present and uses_index and p95_ok)
status = "recovered" if recovered else "not_recovered"
```

`baseline` 从 `incident_repo.get_incident(incident_id).healthy_metrics_baseline` 读取。

- [ ] **Step 7: nodes.py 移除硬编码基线**

`app/agent/nodes.py` 中:删除 `HEALTHY_BASELINE_P95 = 10`;`collect_evidence` 的 E1 判定改为读取 Incident 健康基线:

```python
inc = incident_repo.get_incident(state["incident_id"])
health = (inc.healthy_metrics_baseline or {}) if inc else {}
base_p95 = (health or {}).get("p95_ms")
e1 = r1["success"] and p95 is not None and (base_p95 is None or p95 > int(base_p95) * 1.2)
```

(基线缺失时 E1 只看「P95 非空且明显偏大」——用 `p95 > 100` 兜底,注释说明这是基线缺失时的宽松判定。)

- [ ] **Step 8: 运行测试确认通过 + 全量回归**

Run: `cd ai-service && uv run pytest tests/test_health_baseline.py -q`
Expected: PASS。
Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 41 个原测试全部通过(若有断言依赖 `save_incident_baseline` 名称,同步更新测试)。

- [ ] **Step 9: 端到端回归(M1~M3 不受影响)**

启动 Java 两服务 + AI 服务后运行 `python scripts/verify-m3.py`。
Expected: PASS(恢复判定新增 P95 条件后,索引恢复 + EXPLAIN 命中 + P95 回落,应仍 recovered)。

- [ ] **Step 10: Commit**

```bash
git add ai-service/
git commit -m "fix(agent): 恢复判定改用 Incident 真实健康基线(E1/verify_recovery)"
```

---

### Task 4.4: 后端补丁 B — 状态变化事件写入 + SSE 终态关闭

**Files:**
- Modify: `ai-service/app/agent/nodes.py`(状态变更点追加 `status_changed` / `incident_finished` 事件)
- Modify: `ai-service/app/api/stream.py`(终态后发最终事件并关闭连接)
- Test: `ai-service/tests/test_status_events.py`(新建)

**Interfaces:**
- Consumes: `event_repo.append_event(incident_id, event_type, payload)`(现有)。
- Produces: 事件类型新增 `status_changed`(payload `{"status": "..."}`)与 `incident_finished`(payload `{"status": "终态"}`);SSE 流在 Incident 进入终态、发完事件后结束(而非无限轮询)。

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_status_events.py`:

```python
"""状态变化事件写入与 SSE 终态关闭。"""
import pytest
from sqlalchemy import select

from app.db.engine import get_control_engine
from app.db.models import IncidentEvent
from app.repositories import incident_repo, run_repo
from app.agent import nodes
from app.services import runner


@pytest.mark.asyncio
async def test_start_investigation_emits_status_events(tmp_path):
    """调查启动后 incident_event 中存在 status_changed(investigating)。"""
    inc = incident_repo.create_incident("evt-test", None, "low", "inventory-service")
    run = run_repo.create_run(inc.id)
    await runner.start_investigation(inc.id, run.id, run.thread_id)
    await _wait_terminal(run.id)
    with get_control_engine().connect() as conn:
        types = [e.event_type for e in conn.execute(
            select(IncidentEvent).where(IncidentEvent.incident_id == inc.id))]
    assert "status_changed" in types
    statuses = [e for e in types if e == "status_changed"]
    assert statuses, "应至少有一个 status_changed 事件"


@pytest.mark.asyncio
async def _wait_terminal(run_id: int, timeout_s: float = 15.0):
    import asyncio
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        run = run_repo.get_run(run_id)
        if run.status not in {"created", "investigating", "executing", "verifying"}:
            return run
        await asyncio.sleep(0.2)
    raise AssertionError(f"run {run_id} 未在 {timeout_s}s 内到达终态")```

(若 `runner` 没有 `wait_run`,在测试中用轮询 `run_repo.get_run(run.id).status` 直至终态,超时 15s 断言失败。)

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_status_events.py -q`
Expected: FAIL(当前无 status_changed 事件)。

- [ ] **Step 3: nodes.py 各状态变更点追加事件**

在 `nodes.py` 引入 `from app.repositories.event_repo import append_event`,并在以下节点状态写入后追加:

```python
append_event(state["incident_id"], "status_changed", {"status": state["status"]})
```

具体位置:
- `ingest` / `hypothesize` 结束(状态 `investigating`);
- `diagnose` 中 `needs_human` 分支;
- `propose_fix` 结束(状态 `awaiting_approval`);
- `human_approval` 两个分支(`executing` / `rejected`);
- `execute_fix` 的 `failed` 分支与正常分支;
- `verify_recovery_node` 两个分支(`recovered` / `needs_human`)。

`report` 节点在写入 postmortem 后追加终态事件:

```python
append_event(state["incident_id"], "incident_finished", {"status": state["status"]})
```

- [ ] **Step 4: stream.py 终态关闭**

`app/api/stream.py` 的 `_event_stream` 轮询循环中,每次拿到新事件后(或空转前)检查 Incident 是否终态:

```python
inc = incident_repo.get_incident(incident_id)
if inc is not None and inc.status in TERMINAL_STATUSES:
    yield _format_event(type("Ev", (), {"event_type": "incident_finished",
                                        "payload": {"status": inc.status},
                                        "sequence": last})())
    return
```

`TERMINAL_STATUSES = {"recovered", "needs_human", "rejected", "failed"}` 定义在模块顶部。放在"发完新事件后"检查,保证终态事件不重复且连接关闭。

- [ ] **Step 5: 运行测试确认通过 + 全量回归**

Run: `cd ai-service && uv run pytest tests/test_status_events.py tests/test_stream.py -q`
Expected: PASS。
Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全绿。

- [ ] **Step 6: Commit**

```bash
git add ai-service/
git commit -m "feat(agent): 状态变化事件写入与 SSE 终态关闭"
```

---

### Task 4.5: 场景与事件列表页

**Files:**
- Create: `web/src/views/ScenarioView.vue`
- Create: `web/src/components/ScenarioControl.vue`
- Test: `web/src/__tests__/ScenarioView.test.ts`(注意:测试文件在 `src/__tests__/`,而 vitest include 是 `src/**/*.test.ts`,两者都匹配——保持 include 即可,路径放 `src/views/ScenarioView.test.ts` 更简单)

**Interfaces:**
- Consumes: `client.getScenarioStatus()`、`injectScenario()`、`resetScenario()`、`createIncident()`、`listIncidents()`;`STATUS_META`。
- Produces: 页面含场景卡片(当前 `indexPresent` 状态、注入/重置按钮、创建 Incident 表单)与 Incident 列表(状态筛选 + 每行"查看"/"开始调查"入口);创建成功跳转详情页。

- [ ] **Step 1: 写失败测试**

`web/src/views/ScenarioView.test.ts`:

```ts
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ScenarioView from './ScenarioView.vue'
import * as client from '@/api/client'

vi.mock('@/api/client', () => ({
  getScenarioStatus: vi.fn(),
  injectScenario: vi.fn(),
  resetScenario: vi.fn(),
  createIncident: vi.fn(),
  listIncidents: vi.fn(),
}))

const mocked = vi.mocked(client)

describe('ScenarioView', () => {
  it('渲染场景状态与 Incident 列表', async () => {
    mocked.getScenarioStatus.mockResolvedValue({ indexPresent: true })
    mocked.listIncidents.mockResolvedValue([
      { id: 1, title: '库存慢查询', status: 'awaiting_approval', severity: 'medium', created_at: '2026-08-10 00:00:00' },
    ])
    const wrapper = mount(ScenarioView)
    await flushPromises()
    expect(wrapper.text()).toContain('库存慢查询')
    expect(wrapper.text()).toContain('健康') // indexPresent=true 展示健康
  })

  it('创建 Incident 后调用 API 并跳转', async () => {
    mocked.getScenarioStatus.mockResolvedValue({ indexPresent: false })
    mocked.listIncidents.mockResolvedValue([])
    mocked.createIncident.mockResolvedValue({ id: 9, status: 'created', title: 't', service_ref: 'inventory-service' })
    const push = vi.fn()
    const wrapper = mount(ScenarioView, {
      global: { mocks: { $router: { push } } },
    })
    await flushPromises()
    await wrapper.find('input[placeholder*="标题"]').setValue('慢SQL 事件')
    await wrapper.find('button').trigger('click')
    await flushPromises()
    expect(mocked.createIncident).toHaveBeenCalled()
    expect(push).toHaveBeenCalledWith('/incidents/9')
  })
})
```

(表单组件与按钮数量以实际实现为准,测试写清楚 `data-testid` 或唯一文案选择器。若按钮难以选择,给按钮加 `data-testid="create-incident"` 等。)

- [ ] **Step 2: 运行测试确认失败**

Run: `cd web && npx vitest run src/views/ScenarioView.test.ts`
Expected: FAIL(视图为占位)。

- [ ] **Step 3: 实现 ScenarioControl.vue 与 ScenarioView.vue**

`ScenarioControl.vue`:场景卡片(`indexPresent` → 健康/故障标签)、注入故障按钮(`injectScenario`,确认弹窗)、重置环境按钮(`resetScenario`,确认弹窗)、创建 Incident 表单(el-form:标题必填、service_ref 下拉固定 `inventory-service`、severity 下拉 low/medium/high、description 可选)。创建成功后 `emit('created', id)`。

`ScenarioView.vue`:加载场景状态 + 列表;`el-select` 按状态筛选;每行操作按钮「查看」(`/incidents/:id`)与「开始调查」(仅 `created` 状态,调用 `startInvestigation` 后刷新)。收到 `created` 事件后 `router.push('/incidents/' + id)`。每 5s 轮询 `getScenarioStatus` 与 `listIncidents`(组件卸载时清除定时器)。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd web && npx vitest run src/views/ScenarioView.test.ts`
Expected: PASS。

- [ ] **Step 5: 手动验证(需后端在跑)**

启动 AI + Java 服务后,`npm run dev` 打开 `http://localhost:5173`,确认:场景状态展示、注入/重置生效、创建 Incident 后跳转详情。

- [ ] **Step 6: Commit**

```bash
git add web/src
git commit -m "feat(web): 场景与事件列表页(注入/重置/创建/筛选)"
```

---

### Task 4.6: 调查详情页(轮询版,含审批面板)

**Files:**
- Create: `web/src/views/IncidentDetailView.vue`
- Create: `web/src/components/StatusTag.vue`
- Create: `web/src/components/ApprovalPanel.vue`
- Create: `web/src/components/HypothesisList.vue`
- Create: `web/src/components/EvidenceTable.vue`
- Test: `web/src/views/IncidentDetailView.test.ts`、`web/src/components/ApprovalPanel.test.ts`

**Interfaces:**
- Consumes: `client.getIncident(id)`、`startInvestigation(id)`、`decideApproval(...)`、`getRun(...)`;`STATUS_META`、`isTerminal`。
- Produces: 详情页轮询 `getIncident`(非终态每 3s;终态停止);`created` 状态显示「开始调查」;`awaiting_approval` 且存在 `approval.status === 'pending'` 时显示审批面板(批准/拒绝 + 备注,提交后禁用按钮防重复);`needs_human` 显示人工介入提示;报告入口(终态时)。

- [ ] **Step 1: 写失败测试(审批面板)**

`web/src/components/ApprovalPanel.test.ts`:

```ts
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ApprovalPanel from './ApprovalPanel.vue'
import * as client from '@/api/client'

vi.mock('@/api/client', () => ({ decideApproval: vi.fn() }))

const props = {
  incidentId: 5,
  approvalId: 11,
  status: 'pending' as const,
  incidentStatus: 'awaiting_approval' as const,
}

describe('ApprovalPanel', () => {
  it('pending 且 awaiting_approval 时显示批准/拒绝按钮', () => {
    const wrapper = mount(ApprovalPanel, { props })
    expect(wrapper.text()).toContain('批准')
    expect(wrapper.text()).toContain('拒绝')
  })

  it('非 pending 审批不显示操作按钮', () => {
    const wrapper = mount(ApprovalPanel, { props: { ...props, status: 'approved' } })
    expect(wrapper.find('button').exists()).toBe(false)
  })

  it('提交后禁用按钮防止重复审批', async () => {
    vi.mocked(client.decideApproval).mockResolvedValue({})
    const wrapper = mount(ApprovalPanel, { props })
    await wrapper.find('[data-testid="approve"]').trigger('click')
    await flushPromises()
    expect(client.decideApproval).toHaveBeenCalledWith(5, 11, 'approved', '')
    expect(wrapper.find('[data-testid="approve"]').attributes('disabled')).toBeDefined()
  })
})
```

- [ ] **Step 2: 写失败测试(详情页)**

`web/src/views/IncidentDetailView.test.ts`:mock `client.getIncident` 返回一个含假设/证据/审批/恢复的完整 `IncidentDetail`,断言页面渲染假设描述、证据来源、恢复结论;`created` 状态显示「开始调查」;`needs_human` 状态显示人工介入提示。

- [ ] **Step 3: 运行测试确认失败**

Run: `cd web && npx vitest run src/components/ApprovalPanel.test.ts src/views/IncidentDetailView.test.ts`
Expected: FAIL。

- [ ] **Step 4: 实现组件**

`StatusTag.vue`:`props.status: IncidentStatus`,按 `STATUS_META` 渲染 `<el-tag :type="meta.tag">{{ meta.label }}</el-tag>`。

`ApprovalPanel.vue`:`props { incidentId, approvalId, status, incidentStatus }`;仅当 `status === 'pending' && incidentStatus === 'awaiting_approval'` 显示面板;备注输入(可选);批准/拒绝按钮带 `data-testid="approve"/"reject"`;提交中与已提交时 `disabled`;`decideApproval` 失败用 `ElMessage.error` 提示;成功 `emit('decided')`。

`HypothesisList.vue`:渲染假设卡片(`description` + `el-tag` 按 status:confirmed→success、proposed→info、refuted→danger、supported→primary)。

`EvidenceTable.vue`:渲染证据表(`id/key/source/passed` + `content` JSON 摘要,`:expand` 展开详情)。

`IncidentDetailView.vue`:读取 `route.params.id`;轮询 `getIncident`;页面区块:基本信息描述列表、状态步骤条(按状态机顺序 created→investigating→awaiting_approval→executing→verifying→终态)、「开始调查」按钮(created)、假设列表、证据表、修复方案(approval.fix_proposal_id 所在卡,展示 risk_level 需要时从 report 取)、审批面板、执行结果(`fix_execution.status`)、恢复验证(`recovery` 各字段)、报告入口(终态 →「查看复盘报告」跳 `/incidents/:id/report`)、`needs_human` 警示框。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd web && npx vitest run src/components/ApprovalPanel.test.ts src/views/IncidentDetailView.test.ts`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add web/src
git commit -m "feat(web): 调查详情页(轮询)+ 审批面板与证据/假设展示"
```

---

### Task 4.7: SSE 实时集成(useIncidentStream + 详情页接入)

**Files:**
- Create: `web/src/composables/useIncidentStream.ts`
- Test: `web/src/composables/useIncidentStream.test.ts`
- Modify: `web/src/views/IncidentDetailView.vue`(接入 stream)

**Interfaces:**
- Consumes: SSE `GET /api/incidents/{id}/stream`(事件类型:`snapshot`、`status_changed`、`tool_call`、`incident_finished`;每条带 `id: sequence`)。
- Produces: `useIncidentStream(incidentId: number)` → `{ status: Ref<IncidentStatus | null>, lastEventId: Ref<number>, connected: Ref<boolean>, close(): void }`。去重:`event.id` 加入 `Set`,重复忽略;`status_changed` 更新 `status`;`incident_finished` 自动 `close()`;`snapshot` 只用于握手(不更新状态)。

- [ ] **Step 1: 写失败测试**

`web/src/composables/useIncidentStream.test.ts`:

```ts
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { useIncidentStream } from './useIncidentStream'

type Handler = (ev: MessageEvent) => void

class FakeEventSource {
  static instances: FakeEventSource[] = []
  onmessage: Handler | null = null
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  constructor(public url: string) {
    FakeEventSource.instances.push(this)
  }
  close() { this.closed = true }
  static dispatch(id: number, event: string, data: string) {
    for (const inst of FakeEventSource.instances) {
      const msg = new MessageEvent('message', { data, lastEventId: String(id) })
      ;(msg as any).event = event
      inst.onmessage?.(msg)
    }
  }
}

beforeEach(() => { FakeEventSource.instances = [] })

describe('useIncidentStream', () => {
  it('按 event.id 去重', async () => {
    const stream = useIncidentStream(3)
    FakeEventSource.dispatch(1, 'status_changed', JSON.stringify({ status: 'investigating' }))
    FakeEventSource.dispatch(1, 'status_changed', JSON.stringify({ status: 'investigating' }))
    await nextTick()
    expect(stream.status.value).toBe('investigating')
    FakeEventSource.dispatch(2, 'status_changed', JSON.stringify({ status: 'awaiting_approval' }))
    await nextTick()
    expect(stream.status.value).toBe('awaiting_approval')
    stream.close()
  })

  it('incident_finished 自动关闭连接', async () => {
    const stream = useIncidentStream(3)
    FakeEventSource.dispatch(1, 'incident_finished', JSON.stringify({ status: 'recovered' }))
    await nextTick()
    expect(stream.status.value).toBe('recovered')
    expect(FakeEventSource.instances[0].closed).toBe(true)
    stream.close()
  })
})
```

(测试中需要 mock 全局 `EventSource`:在测试文件顶部 `vi.stubGlobal('EventSource', FakeEventSource)`,`afterEach(() => vi.unstubAllGlobals())`。)

- [ ] **Step 2: 运行测试确认失败**

Run: `cd web && npx vitest run src/composables/useIncidentStream.test.ts`
Expected: FAIL(模块不存在)。

- [ ] **Step 3: 实现 useIncidentStream.ts**

```ts
import { onBeforeUnmount, ref, type Ref } from 'vue'
import type { IncidentStatus } from '@/api/types'

export interface IncidentStream {
  status: Ref<IncidentStatus | null>
  lastEventId: Ref<number>
  connected: Ref<boolean>
  close: () => void
}

const TERMINAL: ReadonlySet<IncidentStatus> = new Set(['recovered', 'needs_human', 'rejected', 'failed'])

export function useIncidentStream(incidentId: number): IncidentStream {
  const status = ref<IncidentStatus | null>(null)
  const lastEventId = ref(0)
  const connected = ref(false)
  let es: EventSource | null = null
  const seen = new Set<number>()

  function handle(ev: MessageEvent) {
    const seq = Number(ev.lastEventId || 0)
    if (seq > 0) {
      if (seen.has(seq)) return
      seen.add(seq)
      if (seq > lastEventId.value) lastEventId.value = seq
    }
    const event = (ev as unknown as { event?: string }).event ?? 'message'
    let data: Record<string, unknown> = {}
    try { data = JSON.parse(String(ev.data)) } catch { /* 忽略非 JSON */ }
    if (event === 'status_changed' || event === 'incident_finished') {
      const s = data.status as IncidentStatus
      if (s && Object.hasOwn(TERMINAL, s)) status.value = s
    }
    if (event === 'incident_finished') close()
  }

  function close() {
    es?.close()
    es = null
    connected.value = false
  }

  es = new EventSource(`/api/incidents/${incidentId}/stream`)
  es.onopen = () => { connected.value = true }
  es.onmessage = handle
  es.onerror = () => { connected.value = false } // 浏览器自动重连

  onBeforeUnmount(close)
  return { status, lastEventId, connected, close }
}
```

注意:`Object.hasOwn(TERMINAL, s)` 需 `s` 为 string;TS 下改为 `s != null && TERMINAL.has(s)`(Set 用 `.has`)。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd web && npx vitest run src/composables/useIncidentStream.test.ts`
Expected: PASS。

- [ ] **Step 5: 详情页接入**

`IncidentDetailView.vue`:挂载时 `useIncidentStream(Number(route.params.id))`;`watch(status)` 变化时调用 `refresh()` 重拉详情;`isTerminal(status)` 为真时停止 3s 轮询;终态时展示「查看复盘报告」入口。`status_changed` 到达 `awaiting_approval` 时审批面板自动出现。

- [ ] **Step 6: 回归测试**

Run: `cd web && npx vitest run`
Expected: 全部 PASS。

- [ ] **Step 7: Commit**

```bash
git add web/src
git commit -m "feat(web): SSE 实时调查过程(去重/终态关闭)接入详情页"
```

---

### Task 4.8: 复盘报告页

**Files:**
- Create: `web/src/views/ReportView.vue`
- Test: `web/src/views/ReportView.test.ts`

**Interfaces:**
- Consumes: `client.getIncident(id).report`(dict:`summary`、`timeline`、`root_cause`、`evidence`、`approval`、`fix`、`recovery`、`open_issues` 等字段,均为可选,容错渲染)。
- Produces: 报告页展示:故障摘要、调查时间线、根因与证据、审批信息、修复动作、恢复结论、未解决问题;`report` 为空或 `null` 时显示"报告尚未生成";提供「返回详情」链接。

- [ ] **Step 1: 写失败测试**

`web/src/views/ReportView.test.ts`:

```ts
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ReportView from './ReportView.vue'
import * as client from '@/api/client'

vi.mock('@/api/client', () => ({ getIncident: vi.fn() }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { id: '7' } }) }))

const report = {
  summary: 'inventory 查询全表扫描',
  root_cause: '缺少联合索引 idx_sku_warehouse',
  timeline: ['investigating', 'awaiting_approval', 'recovered'],
  approval: { decision: 'approved', approver: 'demo-approver' },
  fix: { action_type: 'create_index', status: 'succeeded' },
  recovery: { status: 'recovered', p95_after_ms: 2 },
  open_issues: [],
}

describe('ReportView', () => {
  it('渲染报告各区块', async () => {
    vi.mocked(client.getIncident).mockResolvedValue({
      id: 7, title: '慢查询', status: 'recovered', severity: 'medium', service_ref: 'inventory-service',
      created_at: 'x', finished_at: 'y', hypotheses: [], evidence: [], approvals: [],
      fix_execution: null, recovery: null, report,
    })
    const wrapper = mount(ReportView)
    await flushPromises()
    expect(wrapper.text()).toContain('inventory 查询全表扫描')
    expect(wrapper.text()).toContain('缺少联合索引 idx_sku_warehouse')
  })

  it('报告为空时提示未生成', async () => {
    vi.mocked(client.getIncident).mockResolvedValue({
      id: 7, title: '慢查询', status: 'investigating', severity: 'medium', service_ref: 'inventory-service',
      created_at: 'x', finished_at: null, hypotheses: [], evidence: [], approvals: [],
      fix_execution: null, recovery: null, report: null,
    })
    const wrapper = mount(ReportView)
    await flushPromises()
    expect(wrapper.text()).toContain('尚未生成')
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd web && npx vitest run src/views/ReportView.test.ts`
Expected: FAIL。

- [ ] **Step 3: 实现 ReportView.vue**

按报告 dict 容错渲染(每个区块 `v-if="report && report.xxx"`),缺失字段不报错;`el-descriptions` 展示 key-value;`timeline` 用 `el-timeline`;未解决问题列表为空时显示"无"。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd web && npx vitest run src/views/ReportView.test.ts`
Expected: PASS。

- [ ] **Step 5: 全量测试 + 构建**

Run: `cd web && npx vitest run && npm run build`
Expected: 全部 PASS + BUILD SUCCESS。

- [ ] **Step 6: Commit**

```bash
git add web/src
git commit -m "feat(web): 复盘报告页"
```

---

## M4 验收(对应设计文档 §10)

1. **自动化**:`cd web && npx vitest run` 全绿;`npm run build` 成功;`cd ai-service && uv run pytest tests/` 全绿(含新增基线/事件测试);`python scripts/verify-m3.py` PASS(恢复判定改动后回归)。
2. **手动演示**(浏览器):启动 MySQL + Java 两服务 + AI 服务 + `npm run dev`;打开 `http://localhost:5173`,完整走一遍:重置环境 → 注入故障 → 创建 Incident → 开始调查(详情页 SSE 实时推进状态与工具调用)→ 等待审批 → 批准 → 恢复 → 查看复盘报告。全程不需要命令行。
3. **回归**:`verify-m3-expiry.py` PASS(审批过期路径未受影响)。
