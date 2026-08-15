# Agent Run 观测面板 + 量化评测报告 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 TraceMind 增加一次 Agent 运行的完整轨迹观测(聚合 + 卡点诊断 + 前端可视化),并用真实模型产出量化评测报告。

**Architecture:** 后端新增只读观测聚合端点(GET /api/incidents/{id}/runs/{run_id}/observation),把已落库的 model_call / tool_call(+tool_call_attempt) / retrieval_record 按 run 聚合,并归纳卡点异常(anomalies);前端新增 RunObservationView 可视化;另加 eval_agent_report.py 跑真实模型并产出 markdown 报告。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.0(control 库 raw SQL)/ Vue 3 + Element Plus + Vitest / requests。

## Global Constraints

- 观测端点只读,不触碰任何写路径(不改 agent 运行逻辑)。
- 沿用 V1.6 决定:GitHub 纯远程仓库,不做 CI,验证手动(`cd ai-service && .venv/Scripts/pytest.exe tests/ -q`)。
- 不引入新第三方依赖(前端复用 Element Plus;后端复用 requests/sqlalchemy)。
- 量化评测规模 = SCN-001 + SCN-002 各 3 轮,真实模型 real_strict + prometheus/jaeger 后端;遇额度不足(429/额度类错误)立即停下并告知用户,不自动换模型。
- 前端不做 Playwright E2E,单测用 Vitest。
- 版本:V1.8。

## File Structure

- `ai-service/app/repositories/model_call_repo.py`(Modify):加 `list_model_calls_by_run`。
- `ai-service/app/repositories/retrieval_repo.py`(Modify):加 `list_retrievals_by_run`。
- `ai-service/app/repositories/tool_repo.py`(Modify):加 `list_tool_call_attempts_by_run`。
- `ai-service/app/services/observation_service.py`(Create):聚合 + 诊断核心逻辑。
- `ai-service/app/api/observation.py`(Create):只读端点路由。
- `ai-service/app/main.py`(Modify):挂载 observation 路由。
- `ai-service/tests/test_observation_service.py`(Create):聚合/诊断单元测试。
- `ai-service/tests/test_observation_api.py`(Create):端点集成测试。
- `web/src/api/client.ts`(Modify):加 `fetchRunObservation` + 类型。
- `web/src/views/RunObservationView.vue`(Create):观测面板视图。
- `web/src/router/index.ts`(Modify):加路由。
- `web/src/views/IncidentDetailView.vue`(Modify):加入口。
- `web/src/views/RunObservationView.test.ts`(Create):前端单测。
- `scripts/eval_agent_report.py`(Create):量化评测报告脚本。

---

### Task 1:repo 读取方法(model_call / retrieval / tool_call_attempt)

**Files:**
- Modify: `ai-service/app/repositories/model_call_repo.py`
- Modify: `ai-service/app/repositories/retrieval_repo.py`
- Modify: `ai-service/app/repositories/tool_repo.py`
- Test: `ai-service/tests/test_observation_repos.py`

**Interfaces:**
- Consumes: `app.db.engine.get_control_engine`(返回可 `.connect()` 的 engine,raw SQL 用 `text()`)。
- Produces:
  - `list_model_calls_by_run(agent_run_id: int) -> list[dict]` — 按 `id` 升序,每行 `dict(r._mapping)`。
  - `list_retrievals_by_run(agent_run_id: int) -> list[dict]` — 同上。
  - `list_tool_call_attempts_by_run(agent_run_id: int) -> list[dict]` — 关联 tool_call 拿 tool_name,按 attempt.id 升序。

- [ ] **Step 1: Write the failing test**

```python
# ai-service/tests/test_observation_repos.py
"""repo 读取方法:用 FakeEngine 验证 SQL 过滤 + 结果映射(不连真实库)。"""
from sqlalchemy import text

from app.db import engine as db_engine


class FakeResult:
    def __init__(self, rows):
        self._rows = rows
        self.last_sql = None
        self.last_params = None

    def _mapping_rows(self):
        return self._rows


class FakeConn:
    def __init__(self, rows_by_sql=None):
        self.rows_by_sql = rows_by_sql or {}
        self.last_sql = None
        self.last_params = None

    def execute(self, stmt, params=None):
        self.last_sql = str(stmt)
        self.last_params = params
        key = str(stmt).split(" FROM ")[0]
        return FakeResult(self.rows_by_sql.get(key, []))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeEngine:
    def __init__(self, rows_by_sql=None):
        self.conn = FakeConn(rows_by_sql)

    def connect(self):
        return self.conn


def _mk_row(**kw):
    class R(dict):
        @property
        def _mapping(self):
            return self
    return R(**kw)


def test_list_model_calls_by_run_filters_and_maps(monkeypatch):
    from app.repositories import model_call_repo
    fake = FakeEngine({"SELECT": [_mk_row(node="hypothesize", latency_ms=100,
                                          input_tokens=10, output_tokens=5)]})
    monkeypatch.setattr(db_engine, "get_control_engine", lambda: fake)
    rows = model_call_repo.list_model_calls_by_run(agent_run_id=7)
    assert len(rows) == 1
    assert rows[0]["node"] == "hypothesize"
    assert "agent_run_id" in fake.conn.last_sql
    assert fake.conn.last_params == {"r": 7}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_observation_repos.py -q`
Expected: FAIL with `AttributeError: module 'app.repositories.model_call_repo' has no attribute 'list_model_calls_by_run'`

- [ ] **Step 3: Write minimal implementation**

```python
# ai-service/app/repositories/model_call_repo.py 末尾追加
def list_model_calls_by_run(agent_run_id: int) -> list[dict]:
    from sqlalchemy import text
    control_engine = get_control_engine()
    with control_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT node, mode, provider, model, model_snapshot, prompt_version, "
            "prompt_hash, tool_schema_version, logical_call_id, attempts_json, "
            "finish_reason, structured_output_valid, tool_call_count, fallback_executor, "
            "latency_ms, input_tokens, output_tokens, status, error_code, degraded, "
            "knowledge_chunk_ids, git_commit_sha, id "
            "FROM model_call WHERE agent_run_id = :r ORDER BY id"), {"r": agent_run_id})
        return [dict(row._mapping) for row in rows]
```

```python
# ai-service/app/repositories/retrieval_repo.py 末尾追加
def list_retrievals_by_run(agent_run_id: int) -> list[dict]:
    from sqlalchemy import text
    control_engine = get_control_engine()
    with control_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT node, final_chunk_ids, scores, latency_ms, status, degraded, id "
            "FROM retrieval_record WHERE agent_run_id = :r ORDER BY id"), {"r": agent_run_id})
        return [dict(row._mapping) for row in rows]
```

```python
# ai-service/app/repositories/tool_repo.py 末尾追加
def list_tool_call_attempts_by_run(agent_run_id: int) -> list[dict]:
    from sqlalchemy import text
    control_engine = get_control_engine()
    with control_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT a.attempt_no, a.client_attempt_id, a.mcp_request_id, a.outcome, "
            "a.error_code, a.retryable, a.latency_ms, a.protocol_version, a.trace_id, "
            "t.tool_name, t.transport, a.id "
            "FROM tool_call_attempt a JOIN tool_call t ON a.tool_call_pk = t.id "
            "WHERE a.agent_run_id = :r ORDER BY a.id"), {"r": agent_run_id})
        return [dict(row._mapping) for row in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_observation_repos.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ai-service/app/repositories/model_call_repo.py ai-service/app/repositories/retrieval_repo.py ai-service/app/repositories/tool_repo.py ai-service/tests/test_observation_repos.py
git commit -m "feat(observation): 新增 model_call/retrieval/tool_call_attempt 按 run 读取方法"
```

---

### Task 2:observation_service — 聚合 + 卡点诊断

**Files:**
- Create: `ai-service/app/services/observation_service.py`
- Test: `ai-service/tests/test_observation_service.py`

**Interfaces:**
- Consumes: Task 1 的 `list_model_calls_by_run` / `list_retrievals_by_run` / `list_tool_call_attempts_by_run`;`run_repo` 取 run 基本信息(`get_run` 返回含 status/termination_reason 的对象,若无则从 incident_repo 兜底)。
- Produces: `build_run_observation(incident_id: int, run_id: int) -> dict`,返回 `{"run": {...}, "timeline": [...], "diagnosis": {...}}`(结构见 spec §3.1)。
  - timeline 元素:`{"type": "llm"|"tool"|"retrieval", "phase": str, "startedAt": str|None, "durationMs": int, "detail": dict}`。
  - diagnosis 元素:`{"terminationReason": str, "bottleneckStep": str|None, "anomalies": [{"type": str, "stepId": str|None, "detail": str}]}`。

- [ ] **Step 1: Write the failing test**

```python
# ai-service/tests/test_observation_service.py
import app.services.observation_service as obs


def _llm(**kw):
    base = {"node": "hypothesize", "latency_ms": 100, "input_tokens": 10,
            "output_tokens": 5, "attempts_json": "[]", "fallback_executor": "",
            "structured_output_valid": 1, "knowledge_chunk_ids": ""}
    base.update(kw)
    return base


def _tool(**kw):
    base = {"tool_name": "get_trace", "transport": "mcp_streamable_http",
            "attempt_no": 1, "outcome": "completed", "error_code": None,
            "latency_ms": 200, "trace_id": "t1"}
    base.update(kw)
    return base


def test_anomaly_duplicate_tool_call(monkeypatch):
    monkeypatch.setattr(obs, "list_model_calls_by_run", lambda r: [])
    monkeypatch.setattr(obs, "list_retrievals_by_run", lambda r: [])
    monkeypatch.setattr(obs, "list_tool_call_attempts_by_run",
                        lambda r: [_tool(), _tool(attempt_no=2, trace_id="t2")])
    monkeypatch.setattr(obs, "_run_summary", lambda i, r: {"status": "needs_human",
                                                            "terminationReason": "no_progress"})
    out = obs.build_run_observation(1, 1)
    types = [a["type"] for a in out["diagnosis"]["anomalies"]]
    assert "duplicate_tool_call" in types


def test_anomaly_retry_and_fallback(monkeypatch):
    monkeypatch.setattr(obs, "list_model_calls_by_run",
                        lambda r: [_llm(attempts_json='[{"ok":false}]', fallback_executor="deterministic")])
    monkeypatch.setattr(obs, "list_retrievals_by_run", lambda r: [])
    monkeypatch.setattr(obs, "list_tool_call_attempts_by_run", lambda r: [])
    monkeypatch.setattr(obs, "_run_summary", lambda i, r: {"status": "recovered",
                                                            "terminationReason": None})
    out = obs.build_run_observation(1, 1)
    types = [a["type"] for a in out["diagnosis"]["anomalies"]]
    assert "retry" in types
    assert "fallback_triggered" in types
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_observation_service.py -q`
Expected: FAIL with `ModuleNotFoundError: app.services.observation_service`

- [ ] **Step 3: Write minimal implementation**

```python
# ai-service/app/services/observation_service.py
"""Run 观测聚合 + 卡点诊断(只读)。"""
from app.repositories import (model_call_repo, retrieval_repo, tool_repo)

_PHASE_BY_NODE = {
    "hypothesize": "hypothesize",
    "collect_evidence": "collect_evidence",
    "diagnose": "diagnose",
    "fix": "fix",
    "recovery": "recovery",
}


def _run_summary(incident_id: int, run_id: int) -> dict:
    from app.repositories import run_repo
    run = run_repo.get_run(incident_id, run_id)
    if run is None:
        return {"status": "unknown", "terminationReason": None}
    return {"status": getattr(run, "status", "unknown"),
            "terminationReason": getattr(run, "termination_reason", None)}


def build_run_observation(incident_id: int, run_id: int) -> dict:
    llms = list_model_calls_by_run(run_id)
    tools = list_tool_call_attempts_by_run(run_id)
    retrs = list_retrievals_by_run(run_id)

    timeline = []
    for m in llms:
        attempts = _parse_attempts(m.get("attempts_json") or "")
        timeline.append({
            "type": "llm",
            "phase": _PHASE_BY_NODE.get(m.get("node", ""), m.get("node", "")),
            "startedAt": None,
            "durationMs": m.get("latency_ms") or 0,
            "detail": {
                "node": m.get("node"), "model": m.get("model"),
                "promptVersion": m.get("prompt_version"),
                "inputTokens": m.get("input_tokens"), "outputTokens": m.get("output_tokens"),
                "latencyMs": m.get("latency_ms"), "retries": max(len(attempts) - 1, 0),
                "finishReason": m.get("finish_reason"),
                "structuredOutputValid": bool(m.get("structured_output_valid")),
                "fallbackTriggered": bool(m.get("fallback_executor")),
                "knowledgeChunkIds": [x for x in (m.get("knowledge_chunk_ids") or "").split(",") if x],
            },
        })
    for t in tools:
        timeline.append({
            "type": "tool", "phase": "collect_evidence", "startedAt": None,
            "durationMs": t.get("latency_ms") or 0,
            "detail": {"name": t.get("tool_name"), "transport": t.get("transport"),
                       "attemptNo": t.get("attempt_no"), "outcome": t.get("outcome"),
                       "errorCode": t.get("error_code"), "latencyMs": t.get("latency_ms"),
                       "traceId": t.get("trace_id")},
        })
    for r in retrs:
        timeline.append({
            "type": "retrieval", "phase": "hypothesize", "startedAt": None,
            "durationMs": r.get("latency_ms") or 0,
            "detail": {"hitDocIds": [x for x in (r.get("final_chunk_ids") or "").split(",") if x],
                       "scores": [float(x) for x in (r.get("scores") or "").split(",") if x],
                       "latencyMs": r.get("latency_ms"), "degraded": bool(r.get("degraded"))},
        })

    diagnosis = _diagnose(run_id, llms, tools)
    return {"run": {"runId": run_id, **_run_summary(incident_id, run_id)},
            "timeline": timeline, "diagnosis": diagnosis}


def _parse_attempts(s: str) -> list:
    import json
    try:
        return json.loads(s or "[]")
    except Exception:  # noqa: BLE001
        return []


def _diagnose(run_id: int, llms: list, tools: list) -> dict:
    anomalies = []
    seen_tools = {}
    for t in tools:
        name = t.get("tool_name")
        seen_tools[name] = seen_tools.get(name, 0) + 1
        if t.get("outcome") in ("failed", "error", "outcome_unknown"):
            anomalies.append({"type": "tool_failed", "stepId": None,
                              "detail": f"{name} outcome={t.get('outcome')}"})
    for name, n in seen_tools.items():
        if n >= 2:
            anomalies.append({"type": "duplicate_tool_call", "stepId": None,
                              "detail": f"{name} 调用 {n} 次"})
    for m in llms:
        if max(len(_parse_attempts(m.get("attempts_json") or "")) - 1, 0) > 0:
            anomalies.append({"type": "retry", "stepId": None, "detail": m.get("node")})
        if m.get("fallback_executor"):
            anomalies.append({"type": "fallback_triggered", "stepId": None,
                              "detail": m.get("node")})
        if not m.get("structured_output_valid"):
            anomalies.append({"type": "structured_output_invalid", "stepId": None,
                              "detail": m.get("node")})
    # bottleneck = 累计时长最长的 phase
    phase_ms = {}
    for m in llms:
        phase_ms[m.get("node")] = phase_ms.get(m.get("node"), 0) + (m.get("latency_ms") or 0)
    bottleneck = max(phase_ms, key=phase_ms.get) if phase_ms else None
    summary = _run_summary(0, run_id)
    if summary.get("terminationReason") == "decision_budget_exhausted":
        anomalies.append({"type": "decision_budget_exhausted", "stepId": None,
                          "detail": "诊断预算耗尽"})
    return {"terminationReason": summary.get("terminationReason"),
            "bottleneckStep": bottleneck, "anomalies": anomalies}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_observation_service.py -q`
Expected: PASS(2 passed)

- [ ] **Step 5: Commit**

```bash
git add ai-service/app/services/observation_service.py ai-service/tests/test_observation_service.py
git commit -m "feat(observation): Run 观测聚合 + 8 类卡点诊断"
```

---

### Task 3:只读观测端点

**Files:**
- Create: `ai-service/app/api/observation.py`
- Modify: `ai-service/app/main.py`
- Test: `ai-service/tests/test_observation_api.py`

**Interfaces:**
- Consumes: Task 2 的 `build_run_observation`;`incident_repo.get_incident`(校验 incident 存在)。
- Produces: `GET /api/incidents/{incident_id}/runs/{run_id}/observation` → 200 `build_run_observation(...)` 结果;404 当 incident 不存在。

- [ ] **Step 1: Write the failing test**

```python
# ai-service/tests/test_observation_api.py
def test_observation_endpoint_ok(monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    import app.services.observation_service as obs

    monkeypatch.setattr(obs, "build_run_observation",
                        lambda i, r: {"run": {"runId": r}, "timeline": [], "diagnosis": {}})
    monkeypatch.setattr("app.repositories.incident_repo.get_incident",
                        lambda i: type("I", (), {"id": i})())
    c = TestClient(main.app)
    resp = c.get("/api/incidents/1/runs/1/observation")
    assert resp.status_code == 200
    assert resp.json()["run"]["runId"] == 1


def test_observation_endpoint_404(monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    monkeypatch.setattr("app.repositories.incident_repo.get_incident", lambda i: None)
    c = TestClient(main.app)
    assert c.get("/api/incidents/999/runs/1/observation").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_observation_api.py -q`
Expected: FAIL(404/路由不存在)

- [ ] **Step 3: Write minimal implementation**

```python
# ai-service/app/api/observation.py
from fastapi import APIRouter, HTTPException

from app.repositories import incident_repo
from app.services.observation_service import build_run_observation

router = APIRouter(prefix="/api/incidents")


@router.get("/{incident_id}/runs/{run_id}/observation")
def get_run_observation(incident_id: int, run_id: int) -> dict:
    if incident_repo.get_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return build_run_observation(incident_id, run_id)
```

`ai-service/app/main.py` 里,在既有 `app.include_router(...)` 处追加:

```python
from app.api import observation
app.include_router(observation.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_observation_api.py -q`
Expected: PASS(2 passed)

- [ ] **Step 5: Commit**

```bash
git add ai-service/app/api/observation.py ai-service/app/main.py ai-service/tests/test_observation_api.py
git commit -m "feat(observation): 只读观测聚合端点 GET /observation"
```

---

### Task 4:前端 client — fetchRunObservation + 类型

**Files:**
- Modify: `web/src/api/client.ts`
- Test: `web/src/api/__tests__/observation.test.ts`

**Interfaces:**
- Consumes: 无(新增)。
- Produces: `fetchRunObservation(incidentId: number, runId: number): Promise<RunObservation>`;`RunObservation` 类型(见 Step 3)。

- [ ] **Step 1: Write the failing test**

```ts
// web/src/api/__tests__/observation.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fetchRunObservation } from '../client'

describe('observation api client', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('fetchRunObservation 请求正确 URL 并解析', async () => {
    const fake = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        run: { runId: 9, status: 'recovered' },
        timeline: [{ type: 'llm', phase: 'diagnose', startedAt: null, durationMs: 100,
                     detail: { node: 'diagnose' } }],
        diagnosis: { terminationReason: null, bottleneckStep: 'diagnose', anomalies: [] }
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    const out = await fetchRunObservation(1, 9)
    expect(fake).toHaveBeenCalledWith('/api/incidents/1/runs/9/observation', expect.anything())
    expect(out.run.runId).toBe(9)
    expect(out.diagnosis.bottleneckStep).toBe('diagnose')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npm run test -- observation.test.ts`
Expected: FAIL(`fetchRunObservation` 不存在)

- [ ] **Step 3: Write minimal implementation**

```ts
// web/src/api/client.ts 末尾追加
export interface RunObservationLlmDetail {
  node?: string; model?: string; promptVersion?: string
  inputTokens?: number | null; outputTokens?: number | null
  latencyMs?: number; retries?: number; finishReason?: string
  structuredOutputValid?: boolean; fallbackTriggered?: boolean
  knowledgeChunkIds?: string[]
}
export interface RunObservationToolDetail {
  name?: string; transport?: string; attemptNo?: number
  outcome?: string; errorCode?: string | null; latencyMs?: number; traceId?: string
}
export interface RunObservationTimelineItem {
  type: 'llm' | 'tool' | 'retrieval'; phase: string
  startedAt: string | null; durationMs: number
  detail: Record<string, unknown>
}
export interface RunObservationAnomaly { type: string; stepId: string | null; detail: string }
export interface RunObservation {
  run: { runId: number; status: string; terminationReason?: string | null }
  timeline: RunObservationTimelineItem[]
  diagnosis: { terminationReason?: string | null; bottleneckStep: string | null; anomalies: RunObservationAnomaly[] }
}

export function fetchRunObservation(incidentId: number, runId: number): Promise<RunObservation> {
  return fetch(`/api/incidents/${incidentId}/runs/${runId}/observation`)
    .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npm run test -- observation.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/src/api/client.ts web/src/api/__tests__/observation.test.ts
git commit -m "feat(web): fetchRunObservation client + RunObservation 类型"
```

---

### Task 5:RunObservationView 视图 + 路由 + 入口

**Files:**
- Create: `web/src/views/RunObservationView.vue`
- Modify: `web/src/router/index.ts`
- Modify: `web/src/views/IncidentDetailView.vue`
- Test: `web/src/views/RunObservationView.test.ts`

**Interfaces:**
- Consumes: Task 4 的 `fetchRunObservation` / `RunObservation`。
- Produces: 路由 `run-observation`(path `/incidents/:id/runs/:runId/observation`);视图组件 `RunObservationView`。

- [ ] **Step 1: Write the failing test**

```ts
// web/src/views/RunObservationView.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import RunObservationView from './RunObservationView.vue'
import * as client from '@/api/client'

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { id: '1', runId: '9' } }),
                             useRouter: () => ({ push: vi.fn() }) }))

describe('RunObservationView', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('渲染诊断摘要与异常徽章', async () => {
    vi.spyOn(client, 'fetchRunObservation').mockResolvedValue({
      run: { runId: 9, status: 'needs_human' },
      timeline: [{ type: 'llm', phase: 'diagnose', startedAt: null, durationMs: 100,
                   detail: { node: 'diagnose', inputTokens: 10, outputTokens: 5 } }],
      diagnosis: { terminationReason: 'no_progress', bottleneckStep: 'diagnose',
                   anomalies: [{ type: 'duplicate_tool_call', stepId: null, detail: 'get_trace x2' }] }
    })
    const w = mount(RunObservationView)
    await flushPromises()
    expect(w.text()).toContain('needs_human')
    expect(w.text()).toContain('duplicate_tool_call')
    expect(w.text()).toContain('diagnose')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npm run test -- RunObservationView.test.ts`
Expected: FAIL(组件文件不存在)

- [ ] **Step 3: Write minimal implementation**

```vue
<!-- web/src/views/RunObservationView.vue -->
<template>
  <div class="obs-view" data-testid="run-observation">
    <el-page-header content="运行观测" @back="goBack" />

    <el-card v-if="data" shadow="never" class="summary">
      <template #header>诊断摘要</template>
      <el-tag :type="data.run.status === 'recovered' ? 'success' : 'warning'">
        {{ data.run.status }}
      </el-tag>
      <span v-if="data.diagnosis.terminationReason" class="reason">
        归因:{{ data.diagnosis.terminationReason }}
      </span>
      <div class="anomalies">
        <el-tag v-for="a in data.diagnosis.anomalies" :key="a.type" type="danger" size="small">
          {{ a.type }}
        </el-tag>
      </div>
    </el-card>

    <el-card v-if="data" shadow="never" class="timeline">
      <template #header>时间线</template>
      <div v-for="(item, i) in data.timeline" :key="i" class="tl-item">
        <el-tag size="small" :type="item.type === 'llm' ? '' : item.type === 'tool' ? 'info' : 'warning'">
          {{ item.type }} / {{ item.phase }}
        </el-tag>
        <span class="dur">{{ item.durationMs }}ms</span>
        <span v-if="item.type === 'llm'" class="detail">
          {{ item.detail.node }} tokens {{ item.detail.inputTokens }}/{{ item.detail.outputTokens }}
        </span>
        <span v-else-if="item.type === 'tool'" class="detail">
          {{ item.detail.name }} {{ item.detail.outcome }}
        </span>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchRunObservation, type RunObservation } from '@/api/client'

const route = useRoute()
const router = useRouter()
const incidentId = Number(route.params.id)
const runId = Number(route.params.runId)
const data = ref<RunObservation | null>(null)

onMounted(async () => {
  try { data.value = await fetchRunObservation(incidentId, runId) }
  catch { data.value = null }
})
function goBack() { router.push(`/incidents/${incidentId}`) }
</script>

<style scoped>
.obs-view { max-width: 960px; margin: 0 auto; }
.summary, .timeline { margin-top: 16px; }
.reason { margin-left: 12px; color: #606266; }
.anomalies { margin-top: 8px; display: flex; gap: 8px; flex-wrap: wrap; }
.tl-item { padding: 6px 0; border-bottom: 1px solid #f0f0f0; }
.dur { margin-left: 8px; color: #909399; font-size: 12px; }
.detail { margin-left: 8px; color: #606266; font-size: 13px; }
</style>
```

`web/src/router/index.ts` 追加:

```ts
{ path: '/incidents/:id/runs/:runId/observation', name: 'run-observation',
  component: () => import('@/views/RunObservationView.vue') },
```

`web/src/views/IncidentDetailView.vue` 加入口(以实际字段名为准,用当前选中的 runId):

```ts
function openObservation() {
  router.push(`/incidents/${incidentId}/runs/${runId}/observation`)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npm run test -- RunObservationView.test.ts && npm run typecheck`
Expected: PASS + typecheck 无错误

- [ ] **Step 5: Commit**

```bash
git add web/src/views/RunObservationView.vue web/src/views/RunObservationView.test.ts web/src/router/index.ts web/src/views/IncidentDetailView.vue
git commit -m "feat(web): RunObservationView 观测面板 + 路由 + 入口"
```

---

### Task 6:eval_agent_report.py — 量化评测报告

**Files:**
- Create: `scripts/eval_agent_report.py`

**Interfaces:**
- Consumes: Task 3 的 `GET /api/incidents/{id}/runs/{run_id}/observation`。
- Produces: `main() -> int`(argparse:`--base`、`--order`、`--rounds` 默认 3、`--out-dir` 默认 `reports/evals`);落盘 markdown 报告。

- [ ] **Step 1: Write the failing test**

```python
# scripts/test_eval_agent_report.py
import eval_agent_report as rep


def test_aggregate_stats():
    rounds = [
        {"scenario": "SCN-001", "status": "recovered", "elapsed": 30.0,
         "observation": {"timeline": [{"type": "llm", "durationMs": 1000, "detail": {"inputTokens": 100, "outputTokens": 20}},
                                      {"type": "tool", "durationMs": 200, "detail": {"name": "get_trace"}}],
                         "diagnosis": {"anomalies": []}}},
        {"scenario": "SCN-001", "status": "recovered", "elapsed": 28.0,
         "observation": {"timeline": [{"type": "llm", "durationMs": 900, "detail": {"inputTokens": 90, "outputTokens": 18}}],
                         "diagnosis": {"anomalies": [{"type": "retry", "stepId": None, "detail": "x"}]}}},
    ]
    s = rep.aggregate(rounds)
    assert s["success_rate"] == 1.0
    assert s["avg_elapsed"] == 29.0
    assert s["avg_input_tokens"] == 95
    assert s["avg_tool_calls"] == 0.5
    assert s["anomaly_counts"]["retry"] == 1


def test_render_markdown():
    md = rep.render_markdown("20260814-120000", [
        {"scenario": "SCN-001", "status": "recovered", "elapsed": 30.0,
         "observation": {"timeline": [], "diagnosis": {"anomalies": []}}}],
        {"success_rate": 1.0, "avg_elapsed": 30.0, "avg_input_tokens": 100,
         "avg_output_tokens": 20, "avg_tool_calls": 1.0, "anomaly_counts": {}})
    assert "SCN-001" in md
    assert "success_rate" not in md  # 渲染成人话,非 key
    assert "recovered" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scripts && ..\\ai-service\\.venv\\Scripts\\python.exe -m pytest test_eval_agent_report.py -q`
Expected: FAIL(ModuleNotFoundError: eval_agent_report)

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/eval_agent_report.py
"""真实模型量化评测:跑 SCN-001/002 各 N 轮,拉观测数据汇总成 markdown 报告。"""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import requests


def _api(base: str, path: str, method="get", **kw):
    fn = getattr(requests, method)
    r = fn(f"{base}{path}", timeout=30, **kw)
    r.raise_for_status()
    return r.json()


def _wait_status(base, incident_id, targets, timeout_s=120):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        d = _api(base, f"/api/incidents/{incident_id}")
        if d["status"] in targets:
            return d["status"]
        time.sleep(2)
    return d["status"]


def run_one_round(base: str, scenario: str, round_no: int) -> dict:
    _api(base, f"/api/demo/scenarios/{scenario}/reset", method="post",
         headers={"x-demo-key": "demo-secret-2026"})
    _api(base, f"/api/demo/scenarios/{scenario}/inject", method="post",
         headers={"x-demo-key": "demo-secret-2026"})
    t0 = time.time()
    inc = _api(base, "/api/incidents", method="post",
               json={"title": f"{scenario} eval", "severity": "high",
                     "service_ref": "inventory-service"})
    incident_id = inc["id"]
    run = _api(base, f"/api/incidents/{incident_id}/investigations", method="post")
    run_id = run["run_id"]
    status = _wait_status(base, incident_id, {"awaiting_approval", "needs_human", "recovered"})
    if status == "awaiting_approval":
        d = _api(base, f"/api/incidents/{incident_id}")
        approvals = d.get("approvals") or []
        if approvals:
            _api(base, f"/api/incidents/{incident_id}/approvals/{approvals[0]['id']}/decision",
                 method="post", json={"decision": "approved", "comment": "eval"})
            status = _wait_status(base, incident_id, {"recovered", "needs_human"}, timeout_s=90)
    obs = _api(base, f"/api/incidents/{incident_id}/runs/{run_id}/observation")
    return {"scenario": scenario, "round": round_no, "status": status,
            "elapsed": round(time.time() - t0, 1), "run_id": run_id,
            "observation": obs}


def aggregate(rounds: list) -> dict:
    n = len(rounds)
    recovered = sum(1 for r in rounds if r["status"] == "recovered")
    elapsed = [r["elapsed"] for r in rounds]
    in_tok = [i["detail"]["inputTokens"] for r in rounds for i in r["observation"]["timeline"]
              if i["type"] == "llm" and i["detail"].get("inputTokens")]
    out_tok = [i["detail"]["outputTokens"] for r in rounds for i in r["observation"]["timeline"]
               if i["type"] == "llm" and i["detail"].get("outputTokens")]
    tools = [i for r in rounds for i in r["observation"]["timeline"] if i["type"] == "tool"]
    anomaly_counts = {}
    for r in rounds:
        for a in r["observation"]["diagnosis"].get("anomalies", []):
            anomaly_counts[a["type"]] = anomaly_counts.get(a["type"], 0) + 1
    return {"success_rate": recovered / n if n else 0.0,
            "avg_elapsed": round(sum(elapsed) / n, 1) if n else 0.0,
            "avg_input_tokens": round(sum(in_tok) / len(in_tok), 1) if in_tok else 0.0,
            "avg_output_tokens": round(sum(out_tok) / len(out_tok), 1) if out_tok else 0.0,
            "avg_tool_calls": round(len(tools) / n, 1) if n else 0.0,
            "anomaly_counts": anomaly_counts}


def render_markdown(ts: str, rounds: list, stats: dict) -> str:
    lines = ["# TraceMind 真实模型评测报告(real_strict)", "",
             f"- 时间:{ts}", f"- 成功率:{stats['success_rate'] * 100:.0f}%",
             f"- 平均耗时:{stats['avg_elapsed']}s",
             f"- 平均 tokens:{stats['avg_input_tokens']}/{stats['avg_output_tokens']}(in/out)",
             f"- 平均工具调用:{stats['avg_tool_calls']} 次/轮",
             f"- 卡点分布:{stats['anomaly_counts'] or '无'}", "",
             "| 轮次 | 场景 | 终态 | 耗时 | 工具调用 |", "|---|---|---|---|---|"]
    for r in rounds:
        tools = sum(1 for i in r["observation"]["timeline"] if i["type"] == "tool")
        lines.append(f"| {r['round']} | {r['scenario']} | {r['status']} | {r['elapsed']}s | {tools} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost:8000")
    p.add_argument("--order", default="http://localhost:8081")
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--out-dir", default="reports/evals")
    args = p.parse_args()
    rounds = []
    for scenario in ("SCN-001", "SCN-002"):
        for r in range(1, args.rounds + 1):
            print(f"[{scenario} round{r}] ...", flush=True)
            try:
                rounds.append(run_one_round(args.base, scenario, r))
            except requests.HTTPError as e:
                if "429" in str(e) or "quota" in str(e).lower():
                    print("额度/限流错误,停止。请核对额度或更换模型。", file=sys.stderr)
                    return 2
                raise
    stats = aggregate(rounds)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.out_dir) / f"agent-eval-{ts}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(ts, rounds, stats), encoding="utf-8")
    print(f"\n报告已写入 {out}")
    print(f"成功率 {stats['success_rate']*100:.0f}% 平均耗时 {stats['avg_elapsed']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scripts && ..\\ai-service\\.venv\\Scripts\\python.exe -m pytest test_eval_agent_report.py -q`
Expected: PASS(2 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/eval_agent_report.py scripts/test_eval_agent_report.py
git commit -m "feat(eval): 真实模型量化评测报告脚本(成功率/耗时/token/卡点分布)"
```

---

### Task 7:整体回归 + 真实模型跑报告(验收)

**Files:**
- 无新增代码(回归 + 验收)。

- [ ] **Step 1:后端全量回归**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/ -q`
Expected: 全 PASS(新增 4 个测试文件不破坏既有 355 个)

- [ ] **Step 2:前端回归**

Run: `cd web && npm run typecheck && npm run test`
Expected: typecheck 无错误 + 全部单测 PASS(新增 observation.test.ts + RunObservationView.test.ts)

- [ ] **Step 3:真实模型跑报告(VM,耗额度)**

VM 上切换到真实模型(改 compose 的 `TRACEMIND_LLM_MODE: real_strict` + `METRICS_BACKEND: prometheus` + `TRACE_BACKEND: jaeger`,见记忆 `tracemind-real-model-quota`),重启 ai-service 后:

Run: `python scripts/eval_agent_report.py --base http://<vm-host>:8000 --rounds 3`
Expected: 生成 `reports/evals/agent-eval-*.md`,含成功率/耗时/token/卡点分布;SCN-001 + SCN-002 各 3 轮。
遇 429/额度错误立即停下并告知用户。

- [ ] **Step 4:提交报告**

```bash
git add reports/evals/agent-eval-*.md
git commit -m "docs(eval): 真实模型量化评测报告(SCN-001/002 各 3 轮)"
```

## Self-Review

- **Spec coverage**:spec §3(后端聚合+诊断)→ Task 1-3;§4(前端)→ Task 4-5;§5(报告)→ Task 6-7。全部覆盖。
- **Placeholder scan**:无 TBD/TODO;Task 5 入口按钮处"以实际字段名为准"是唯一软性说明(因 IncidentDetailView 内部 runId 字段名实施时确认),非占位符。
- **Type consistency**:`build_run_observation`(Task 2)签名与 Task 3 端点调用一致;`list_*_by_run`(Task 1)签名与 Task 2 使用一致;`fetchRunObservation`(Task 4)与 Task 5 组件使用一致;`aggregate`/`render_markdown`(Task 6)签名在测试与实现一致。
