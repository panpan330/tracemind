# V1.13 评测平台可视化 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把静态 markdown 评测报告升级为可浏览的评测平台:后端持久化评测记录(eval_run 表 + API),前端可视化展示成功率/耗时/成本及多版本趋势。

**Architecture:** 新增 `eval_run` 表 + repository + `app/api/evals.py` 两个端点;`eval_agent_report.py` 渲染 md 的同时写库(失败降级只出 md);前端新增 `EvalDashboardView.vue`(列表+统计卡)与 `EvalDetailView.vue`(指标卡+轮次明细),纯 Element Plus 零新依赖。

**Tech Stack:** Python 3.12 / SQLAlchemy(control 库)/ FastAPI / Vue 3 + Element Plus + vitest。

## Global Constraints

- 沿用零新增依赖原则:前端图表用纯 Element Plus(el-progress/el-table/el-statistic),不引入 ECharts。
- 写库失败不阻塞报告生成(降级:只出 md 文件,与现状一致)。
- `eval_run` 与 model_call 同库(control),repository 模式一致。
- 成本字段:评测脚本未直接统计成本——用 `MODEL_PRICE_PER_M` × tokens 估算(aggregate 已有 avg_input/output_tokens)。
- 不做评测触发 UI、不做多版本 diff 图、不改现有 md 输出逻辑(增量写库)。
- 沿用 V1.6 决定:不做 CI,验证手动(`cd ai-service && .venv/Scripts/pytest.exe tests/ -q`;前端 `cd web && npm test`)。
- 前端 mock 模式沿用 ScenarioView.test.ts(vitest + @vue/test-utils + mock @/api/client)。

## File Structure

- `ai-service/app/repositories/eval_run_repo.py`(Create):eval_run CRUD。
- `ai-service/app/api/evals.py`(Create):GET /api/evals 列表 + /api/evals/:id 详情。
- `scripts/eval_agent_report.py`(Modify):aggregate 后写 eval_run(失败降级)。
- `ai-service/app/models/` 或 repo 内建表(按项目现有模式)。
- `web/src/views/EvalDashboardView.vue`(Create):评测列表页。
- `web/src/views/EvalDetailView.vue`(Create):评测详情页。
- `web/src/router/index.ts`(Modify):加 /evals 与 /evals/:id 路由。
- `web/src/api/client.ts`(Modify):加 listEvals / getEval。
- `ai-service/tests/test_evals_repo.py`(Create):repo CRUD。
- `ai-service/tests/test_evals_api.py`(Create):API 测试。
- `web/src/views/EvalDashboardView.test.ts`(Create):前端测试。
- `web/src/views/EvalDetailView.test.ts`(Create):前端测试。

---

### Task 1:eval_run 表 + repository

**Files:**
- Create: `ai-service/app/repositories/eval_run_repo.py`
- Test: `ai-service/tests/test_evals_repo.py`

**Interfaces:**
- Produces: `insert_eval_run(...) -> int`、`list_eval_runs() -> list[dict]`、`get_eval_run(id) -> dict | None`。Task 2 的 API 依赖。

- [ ] **Step 1: 写失败测试**

```python
# ai-service/tests/test_evals_repo.py
import pytest
from app.repositories import eval_run_repo


@pytest.fixture
def clean_evals():
    # 若项目有测试库/清理机制则复用;否则用事务回滚或直接 DELETE
    try:
        eval_run_repo.delete_all_for_test()
    except Exception:
        pass
    yield


def test_insert_and_list(clean_evals):
    eval_run_repo.insert_eval_run(
        scenario="SCN-001", rounds=3, success_rate=0.667,
        avg_duration_ms=45000, total_cost=0.02, model_snapshot="qwen3.8-max",
        summary="2/3 recovered", raw_json='{"rounds":[]}')
    rows = eval_run_repo.list_eval_runs()
    assert len(rows) == 1
    assert rows[0]["scenario"] == "SCN-001"
    assert rows[0]["success_rate"] == pytest.approx(0.667)


def test_get_eval_run_detail(clean_evals):
    rid = eval_run_repo.insert_eval_run(
        scenario="SCN-002", rounds=1, success_rate=1.0,
        avg_duration_ms=30000, total_cost=0.01, model_snapshot="qwen3.7-flash",
        summary="recovered", raw_json='{"rounds":[{"round":1}]}')
    row = eval_run_repo.get_eval_run(rid)
    assert row is not None
    assert row["id"] == rid
    assert row["raw_json"].find("rounds") >= 0


def test_get_missing_returns_none(clean_evals):
    assert eval_run_repo.get_eval_run(999999) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_evals_repo.py -v`
Expected: FAIL(`ImportError: cannot import name 'eval_run_repo'`)

- [ ] **Step 3: 实现**

先读 `app/repositories/model_call_repo.py` 确认建表/engine 模式,按同风格实现:

```python
"""eval_run 评测记录 repository(control 库)。"""
from app.db import get_control_engine  # 确认实际 import 路径
from sqlalchemy import text

_EVAL_RUN_DDL = """
CREATE TABLE IF NOT EXISTS eval_run (
    id INT AUTO_INCREMENT PRIMARY KEY,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    scenario VARCHAR(64) NOT NULL,
    rounds INT NOT NULL DEFAULT 0,
    success_rate DECIMAL(5,4) NOT NULL DEFAULT 0,
    avg_duration_ms INT NOT NULL DEFAULT 0,
    total_cost DECIMAL(10,6) NOT NULL DEFAULT 0,
    model_snapshot VARCHAR(128) NOT NULL DEFAULT '',
    summary VARCHAR(255) NOT NULL DEFAULT '',
    raw_json TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _ensure_table():
    with get_control_engine().connect() as conn:
        conn.execute(text(_EVAL_RUN_DDL))
        conn.commit()


def insert_eval_run(*, scenario, rounds, success_rate, avg_duration_ms,
                    total_cost, model_snapshot, summary, raw_json) -> int:
    _ensure_table()
    with get_control_engine().connect() as conn:
        result = conn.execute(text(
            "INSERT INTO eval_run (scenario, rounds, success_rate, avg_duration_ms, "
            "total_cost, model_snapshot, summary, raw_json) "
            "VALUES (:s, :r, :sr, :d, :c, :m, :sum, :raw)"),
            {"s": scenario, "r": rounds, "sr": success_rate, "d": avg_duration_ms,
             "c": total_cost, "m": model_snapshot, "sum": summary, "raw": raw_json})
        conn.commit()
        return result.lastrowid


def list_eval_runs() -> list[dict]:
    _ensure_table()
    with get_control_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT id, created_at, scenario, rounds, success_rate, avg_duration_ms, "
            "total_cost, model_snapshot FROM eval_run ORDER BY id DESC")).fetchall()
        return [dict(r._mapping) for r in rows]


def get_eval_run(eval_run_id: int) -> dict | None:
    _ensure_table()
    with get_control_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM eval_run WHERE id = :id"), {"id": eval_run_id}).fetchone()
        return dict(row._mapping) if row else None


def delete_all_for_test() -> None:
    with get_control_engine().connect() as conn:
        conn.execute(text("DELETE FROM eval_run"))
        conn.commit()
```

(建表用 `CREATE TABLE IF NOT EXISTS`——测试库与生产库同结构;先读 `model_call_repo.py` 确认 engine 获取方式,若用 `create_engine` 直接建连接则对齐。)

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_evals_repo.py -v`
Expected: PASS(3 个用例)

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/repositories/eval_run_repo.py ai-service/tests/test_evals_repo.py
git commit -m "feat(evals): eval_run 表 + repository(CRUD)"
```

---

### Task 2:评测 API

**Files:**
- Create: `ai-service/app/api/evals.py`
- Test: `ai-service/tests/test_evals_api.py`

**Interfaces:**
- Consumes: `eval_run_repo`(Task 1)。
- Produces: `GET /api/evals`(列表,时间倒序)、`GET /api/evals/:id`(详情含 raw_json)。Task 3 的写库脚本可复用;Task 4-5 的前端页面消费这两个端点。

- [ ] **Step 1: 写失败测试**

先读 `app/api/observation.py` 或现有 API 的注册方式(FastAPI router),按同风格:

```python
# ai-service/tests/test_evals_api.py
from fastapi.testclient import TestClient
from app.main import app  # 确认应用入口路径

client = TestClient(app)


def test_evals_list_empty_ok():
    r = client.get("/api/evals")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_evals_detail_missing_404():
    r = client.get("/api/evals/999999")
    assert r.status_code == 404
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_evals_api.py -v`
Expected: FAIL(`404` 或 route 不存在 404)

- [ ] **Step 3: 实现**

```python
# app/api/evals.py
from fastapi import APIRouter, HTTPException
from app.repositories import eval_run_repo

router = APIRouter(prefix="/api/evals", tags=["evals"])


@router.get("")
def list_evals():
    return eval_run_repo.list_eval_runs()


@router.get("/{eval_run_id}")
def get_eval(eval_run_id: int):
    row = eval_run_repo.get_eval_run(eval_run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="eval run not found")
    return row
```

在应用入口(app.main 或 api 聚合处)注册 router(与现有 api 模块一致)。

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_evals_api.py -v`
Expected: PASS(2 个用例)

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/api/evals.py ai-service/tests/test_evals_api.py
git commit -m "feat(evals): GET /api/evals 列表 + /api/evals/:id 详情"
```

---

### Task 3:eval_agent_report.py 写库增强

**Files:**
- Modify: `scripts/eval_agent_report.py`
- Test: `ai-service/tests/test_eval_report_write.py`(或现有 eval 相关测试文件)

**Interfaces:**
- Consumes: `aggregate(rounds)` stats(已有)、`eval_run_repo.insert_eval_run`(Task 1)。
- Produces: 渲染 md 后写 eval_run;失败降级只出 md。Task 5 的验收(跑一次评测看库)依赖。

- [ ] **Step 1: 写失败测试**

```python
# ai-service/tests/test_eval_report_write.py
def test_write_eval_run_on_report(monkeypatch, tmp_path):
    import scripts.eval_agent_report as mod
    inserted = []
    monkeypatch.setattr(mod, "insert_eval_run", lambda **kw: inserted.append(kw) or 1)
    rounds = [{"round": 1, "scenario": "SCN-001", "status": "recovered",
               "elapsed": 45.0,
               "observation": {"timeline": [], "diagnosis": {"anomalies": []}}}]
    stats = mod.aggregate(rounds)
    ts = "2026-08-14T16:00:00"
    out = mod.write_report(ts, rounds, stats, out_dir=tmp_path)  # 若原 main 内联则需重构出可测函数
    assert len(inserted) == 1
    assert inserted[0]["scenario"] == "SCN-001"
```

(若 `write_report` 不存在,需把 `main()` 中的渲染+写库逻辑抽成可测函数 `write_report(ts, rounds, stats, out_dir)`。)

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_eval_report_write.py -v`
Expected: FAIL(`ImportError` 或无 write_report)

- [ ] **Step 3: 实现**

`eval_agent_report.py` 把 main() 中"渲染 md + 写文件"抽为 `write_report(ts, rounds, stats, out_dir)`:

```python
def write_report(ts: str, rounds: list, stats: dict, out_dir: Path) -> Path:
    md = render_markdown(ts, rounds, stats)
    out = out_dir / f"agent-eval-{ts}.md"
    out.write_text(md, encoding="utf-8")
    # V1.13:写库(失败降级,只出 md)
    try:
        from app.repositories.eval_run_repo import insert_eval_run
        scenario = rounds[0]["scenario"] if rounds else ""
        n = len(rounds)
        recovered = sum(1 for r in rounds if r["status"] == "recovered")
        # 成本估算:用 MODEL_PRICE_PER_M × 平均 tokens(简化为总 tokens 估算)
        from app.agent.cost import MODEL_PRICE_PER_M
        unit = MODEL_PRICE_PER_M.get("qwen3.8-max", 0.0)
        total_tokens = (stats.get("avg_input_tokens", 0) + stats.get("avg_output_tokens", 0)) * n
        cost = unit * total_tokens / 1_000_000 if unit else 0.0
        insert_eval_run(
            scenario=scenario, rounds=n,
            success_rate=recovered / n if n else 0.0,
            avg_duration_ms=int(stats.get("avg_elapsed", 0) * 1000),
            total_cost=round(cost, 6),
            model_snapshot="", summary=f"{recovered}/{n} recovered",
            raw_json=json.dumps(rounds, ensure_ascii=False, default=str))
    except Exception as exc:  # noqa: BLE001 写库失败不阻塞报告
        print(f"[warn] eval_run 写库失败(仅输出 md): {exc}", file=sys.stderr)
    return out
```

(成本估算用默认模型单价——注意这里只做展示用途的估算,不追求精确;`raw_json` 存 rounds 明细供详情页展示。)

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_eval_report_write.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/eval_agent_report.py ai-service/tests/test_eval_report_write.py
git commit -m "feat(evals): 评测报告写 eval_run(失败降级只出 md)"
```

---

### Task 4:前端评测列表页 + 详情页

**Files:**
- Modify: `web/src/api/client.ts`、`web/src/router/index.ts`
- Create: `web/src/views/EvalDashboardView.vue`、`web/src/views/EvalDetailView.vue`
- Test: `web/src/views/EvalDashboardView.test.ts`、`web/src/views/EvalDetailView.test.ts`

**Interfaces:**
- Consumes: `GET /api/evals`、`GET /api/evals/:id`(Task 2)。
- Produces: 两个 vue 页面 + 路由 + client 封装。Task 5 的验收渲染依赖。

- [ ] **Step 1: 写失败测试**

```ts
// web/src/views/EvalDashboardView.test.ts(沿用 ScenarioView.test.ts 模式)
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import EvalDashboardView from './EvalDashboardView.vue'
import * as client from '@/api/client'

vi.mock('@/api/client', () => ({
  listEvals: vi.fn(),
  getEval: vi.fn(),
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

const mocked = vi.mocked(client)

beforeEach(() => { vi.clearAllMocks() })

describe('EvalDashboardView', () => {
  it('渲染评测列表', async () => {
    mocked.listEvals.mockResolvedValue([
      { id: 1, created_at: '2026-08-14T08:00:00', scenario: 'SCN-001', rounds: 3,
        success_rate: 0.667, avg_duration_ms: 45000, total_cost: 0.02, model_snapshot: 'qwen3.8-max' },
    ])
    const wrapper = mount(EvalDashboardView, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    expect(wrapper.text()).toContain('SCN-001')
    expect(wrapper.text()).toContain('67%')
  })
})
```

```ts
// web/src/views/EvalDetailView.test.ts
// mock getEval 返回详情,断言指标卡与轮次明细渲染
```

- [ ] **Step 2: 运行确认失败**

Run: `cd web && npx vitest run src/views/EvalDashboardView.test.ts src/views/EvalDetailView.test.ts`
Expected: FAIL(组件不存在)

- [ ] **Step 3: 实现**

`web/src/api/client.ts` 加:

```ts
export async function listEvals() {
  return request.get('/api/evals')
}
export async function getEval(id: number) {
  return request.get(`/api/evals/${id}`)
}
```

`EvalDashboardView.vue`(纯 Element Plus,零新依赖):
- 顶部 el-statistic:总评测数 / 平均成功率 / 平均成本
- el-table:时间 / 场景 / 轮次 / 成功率(el-progress)/ 平均耗时 / 成本 / 详情按钮

`EvalDetailView.vue`:
- el-statistic:成功率 / 平均耗时 / 总成本 / 模型
- el-table:轮次明细(round/scenario/status/elapsed)

`router/index.ts` 加:

```ts
{ path: '/evals', name: 'evals', component: () => import('@/views/EvalDashboardView.vue') },
{ path: '/evals/:id', name: 'eval-detail', component: () => import('@/views/EvalDetailView.vue') },
```

- [ ] **Step 4: 运行确认通过**

Run: `cd web && npx vitest run src/views/EvalDashboardView.test.ts src/views/EvalDetailView.test.ts`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add web/src/api/client.ts web/src/router/index.ts web/src/views/EvalDashboardView.vue web/src/views/EvalDetailView.vue web/src/views/EvalDashboardView.test.ts web/src/views/EvalDetailView.test.ts
git commit -m "feat(evals): 前端评测列表页 + 详情页(纯 Element Plus 零新依赖)"
```

---

### Task 5:整体回归 + 前端 build + 验收

**Files:**
- 全部改动文件。

**Interfaces:**
- 无新接口;验证 Task 1-4 集成。

- [ ] **Step 1: 后端全量测试**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/ -q`
Expected: 全部 PASS(原 416 + 新增,无回归)。若失败,重点检查 eval_run 建表在测试库的兼容(事务/清理)、API router 注册位置。

- [ ] **Step 2: 前端全量测试 + build**

```bash
cd web && npx vitest run
cd web && npx vue-tsc --noEmit && npx vite build
```

Expected: 全部 PASS + build 成功。

- [ ] **Step 3: 冒烟评测写库**

```bash
cd ai-service && TRACEMIND_LLM_MODE=fake TRACEMIND_EVAL_MODE=true \
  .venv/Scripts/python.exe ../scripts/eval_agent_report.py --rounds 1 2>&1 | tail -5
# 或用现有 eval_agent.py 流程跑一次,确认 reports/evals/*.md 生成且 eval_run 表有记录
```

Expected: md 文件生成 + 库中有 eval_run 记录(可用 `curl http://localhost:8000/api/evals` 验证)。

- [ ] **Step 4: 提交(如有修复)**

```bash
git add -A && git commit -m "fix(evals): 回归修复"
```

- [ ] **Step 5: 推送**

```bash
git push origin main
```
(注意:当前 GitHub 网络间歇不可用——若失败记录待推提交数,告知用户稍后重试。)
