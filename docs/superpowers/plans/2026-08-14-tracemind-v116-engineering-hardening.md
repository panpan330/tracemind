# V1.16 工程补强(多臂老虎机 + 成本告警 + 案例淘汰 + 评测触发 UI) 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一次补齐 4 个工程补强项:多臂老虎机探索(ε-greedy)、成本预算告警、失败案例淘汰策略、评测触发 UI。全部默认关闭/零风险,开启才生效。

**Architecture:** 每项独立小改:ModelScorer.best 加 ε-greedy;新 cost 预算检查写 cost_over_budget 事件;memory 加 purge_expired_cases(复用 RunbookStore.delete_filter);后端 POST /api/evals/run + 前端按钮。

**Tech Stack:** Python 3.12 / FastAPI / qdrant(RunbookStore)/ Vue 3 + Element Plus。

## Global Constraints

- 四项全部默认关闭/零风险:ε=0 纯利用(V1.12 兼容)、COST_BUDGET=0 不启用、RETENTION_DAYS=0 不清理、评测按钮显式触发。
- 多臂老虎机仅 ε-greedy(不做 UCB);rng 可注入(测试固定 seed)。
- 成本告警复用 model_call 审计(零新增埋点);聚合异常降级。
- 案例淘汰只删 `case-*-fail` 失败案例(成功案例保留);qdrant 异常降级;删不存在 point 不报错。
- 评测触发:scenario ∈ {SCN-001,SCN-002},rounds ∈ [1,5],非法 400;后台线程跑,一次一个(简单互斥);真实模型耗额度(前端标注)。
- 不做自动充值、不做 LLM-as-judge、不引入新依赖、不碰前端主流程。
- 沿用 V1.6 决定:不做 CI,验证手动(`cd ai-service && .venv/Scripts/pytest.exe tests/ -q`;前端 `cd web && npx vitest run`)。
- 测试模式:后端 FakeEngine/TestClient+monkeypatch(V1.13 教训);前端 mock @/api/client。

## File Structure

- `ai-service/app/config/settings.py`(Modify):加 routing_epsilon/cost_budget/case_retention_days。
- `ai-service/app/agent/model_scorer.py`(Modify):best 加 ε-greedy。
- `ai-service/app/agent/model_router.py`(Modify):route 传 epsilon。
- `ai-service/app/agent/cost.py`(Modify):加 check_cost_budget。
- `ai-service/app/agent/memory.py`(Modify):加 purge_expired_cases。
- `ai-service/app/api/evals.py`(Modify):加 POST /run。
- `web/src/views/EvalDashboardView.vue`(Modify):加运行评测按钮。
- 测试:test_model_scorer.py / test_cost.py / test_memory.py / test_evals_api.py / EvalDashboardView.test.ts 各扩展。

---

### Task 1:settings 补强配置字段

**Files:**
- Modify: `ai-service/app/config/settings.py`
- Test: `ai-service/tests/test_model_scorer.py`(或独立字段测试)

**Interfaces:**
- Produces: `settings.routing_epsilon: float = 0.1`、`settings.cost_budget: float = 0.0`、`settings.case_retention_days: int = 0`。Task 2-4 依赖。

- [ ] **Step 1: 写失败测试**

```python
# ai-service/tests/test_model_scorer.py(追加)
def test_settings_has_hardening_fields():
    from app.config import settings
    assert hasattr(settings, "routing_epsilon")
    assert settings.routing_epsilon == 0.1
    assert settings.cost_budget == 0.0
    assert settings.case_retention_days == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_model_scorer.py::test_settings_has_hardening_fields -v`
Expected: FAIL(`AttributeError`)

- [ ] **Step 3: 实现**

`settings.py` 的 V1.12 动态路由区块后加:

```python
    # ---- V1.16 工程补强(默认关,开启才生效)----
    routing_epsilon: float = 0.1        # ε-greedy 探索概率;0=纯利用(V1.12 兼容)
    cost_budget: float = 0.0            # 累计成本预算(元);0=不启用告警
    case_retention_days: int = 0        # 失败案例保留天数;0=不清理
```

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_model_scorer.py -v`
Expected: PASS(含既有 + 新字段)

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/config/settings.py ai-service/tests/test_model_scorer.py
git commit -m "feat(hardening): V1.16 补强配置字段(默认关,零风险)"
```

---

### Task 2:多臂老虎机探索(ModelScorer.best ε-greedy)

**Files:**
- Modify: `ai-service/app/agent/model_scorer.py`、`ai-service/app/agent/model_router.py`
- Test: `ai-service/tests/test_model_scorer.py`、`ai-service/tests/test_model_router.py`

**Interfaces:**
- Consumes: `settings.routing_epsilon`(Task 1)。
- Produces: `best(node, candidates, epsilon=0.0, rng=None)`:ε 概率随机探索有数据的候选,否则选最优;`route()` 传 `settings.routing_epsilon` 与默认 rng。Task 无下游依赖。

- [ ] **Step 1: 写失败测试**

```python
# ai-service/tests/test_model_scorer.py(追加)
import random


def _feed(sc, node, model, success=True):
    for _ in range(10):
        sc.update(node, model, {"success": success, "latency_ms": 100, "cost": 0.001})


def test_epsilon_zero_picks_best():
    sc = ModelScorer()
    _feed(sc, "select_tool", "flash", True)
    _feed(sc, "select_tool", "max", False)
    assert sc.best("select_tool", ["flash", "max"], epsilon=0.0) == "flash"


def test_epsilon_one_explores_with_seed():
    sc = ModelScorer()
    _feed(sc, "select_tool", "flash", True)
    _feed(sc, "select_tool", "max", False)
    rng = random.Random(42)
    chosen = {sc.best("select_tool", ["flash", "max"], epsilon=1.0, rng=rng)
              for _ in range(50)}
    assert chosen == {"flash", "max"}   # ε=1 全随机,两个候选都可能被选


def test_epsilon_zero_matches_v112():
    """ε=0 时与 V1.12 行为完全一致(选最优)。"""
    sc = ModelScorer()
    _feed(sc, "select_tool", "flash", True)
    _feed(sc, "select_tool", "max", False)
    assert sc.best("select_tool", ["flash", "max"], epsilon=0.0) == "flash"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_model_scorer.py::test_epsilon_zero_picks_best tests/test_model_scorer.py::test_epsilon_one_explores_with_seed -v`
Expected: FAIL(`TypeError: best() got an unexpected keyword argument 'epsilon'`)

- [ ] **Step 3: 实现**

`model_scorer.py` 的 `best`:

```python
    def best(self, node: str, candidates: list[str], epsilon: float = 0.0,
             rng: random.Random | None = None) -> str | None:
        """候选里选评分最高者;ε 概率随机探索有数据的候选(ε-greedy)。"""
        scored = []
        for m in candidates:
            q = self._windows.get((node, m))
            if q is None or len(q) < MIN_SAMPLES:
                continue
            scored.append((self._score(q), m))
        if not scored:
            return None
        scored.sort(key=lambda x: x[0], reverse=True)
        if epsilon > 0 and (rng or random).random() < epsilon:
            return scored[rng.choice(range(len(scored)))][1] if rng else random.choice(scored)[1]
        return scored[0][1]
```

顶部加 `import random`。

`model_router.py` 的 `route`:

```python
            chosen = scorer.best(node, candidates, epsilon=settings.routing_epsilon)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_model_scorer.py tests/test_model_router.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/agent/model_scorer.py ai-service/app/agent/model_router.py ai-service/tests/test_model_scorer.py ai-service/tests/test_model_router.py
git commit -m "feat(hardening): 多臂老虎机 ε-greedy — 动态路由探索/利用权衡(ε=0 兼容)"
```

---

### Task 3:成本预算告警

**Files:**
- Modify: `ai-service/app/agent/cost.py`
- Test: `ai-service/tests/test_cost.py`

**Interfaces:**
- Consumes: `settings.cost_budget`(Task 1)、model_call 聚合(已有 aggregate_model_costs)。
- Produces: `check_cost_budget(calls) -> bool`:累计成本超预算 → 写 `cost_over_budget` 事件(经 event_repo.append_event,incident_id=0 全局)并返回 True;预算 0/未超 → False;异常降级不抛。Task 无下游依赖(独立告警)。

- [ ] **Step 1: 写失败测试**

```python
# ai-service/tests/test_cost.py(追加)
def test_check_cost_budget_under_budget(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "cost_budget", 100.0)
    calls = [{"model": "qwen3.7-flash", "input_tokens": 100, "output_tokens": 50}]
    assert cost.check_cost_budget(calls) is False


def test_check_cost_budget_over_budget(monkeypatch):
    from app.config import settings
    from app.repositories import event_repo
    monkeypatch.setattr(settings, "cost_budget", 0.00001)   # 极小预算必超
    appended = []
    monkeypatch.setattr(event_repo, "append_event",
                        lambda *a, **k: appended.append((a, k)) or type("E", (), {"sequence": 1})())
    calls = [{"model": "qwen3.7-flash", "input_tokens": 100000, "output_tokens": 50000}]
    assert cost.check_cost_budget(calls) is True
    assert appended, "超预算应写 cost_over_budget 事件"


def test_check_cost_budget_disabled(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "cost_budget", 0.0)
    assert cost.check_cost_budget([]) is False
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_cost.py::test_check_cost_budget_over_budget -v`
Expected: FAIL(`AttributeError: module 'app.agent.cost' has no attribute 'check_cost_budget'`)

- [ ] **Step 3: 实现**

`cost.py` 加:

```python
def check_cost_budget(calls: list[dict]) -> bool:
    """累计成本超预算 → 写 cost_over_budget 事件并返回 True;预算 0/未超 → False。"""
    budget = settings.cost_budget
    if not budget:
        return False
    total = sum(v["cost"] for v in aggregate_model_costs(calls).values())
    if total <= budget:
        return False
    try:
        from app.repositories import event_repo
        event_repo.append_event(0, "cost_over_budget",
                                {"budget": budget, "cost": round(total, 6)})
    except Exception:  # noqa: BLE001 告警失败不影响
        pass
    return True
```

顶部加 `from app.config import settings`(确认是否已 import)。

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_cost.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/agent/cost.py ai-service/tests/test_cost.py
git commit -m "feat(hardening): 成本预算告警 — 超预算写 cost_over_budget 事件(默认关)"
```

---

### Task 4:失败案例淘汰策略

**Files:**
- Modify: `ai-service/app/agent/memory.py`
- Test: `ai-service/tests/test_memory.py`

**Interfaces:**
- Consumes: `settings.case_retention_days`(Task 1)、`RunbookStore.delete_filter(doc_id)`(已存在,按 doc_id 删)。
- Produces: `purge_expired_cases(store=None)`:扫 qdrant `-fail` 案例,超过保留期删除;retention=0 不启用;异常降级。report 节点沉淀后调用。

- [ ] **Step 1: 写失败测试**

```python
# ai-service/tests/test_memory.py(追加)
def test_purge_expired_cases_deletes_old_fail(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.config import settings
    monkeypatch.setattr(settings, "case_retention_days", 7)
    deleted = []
    store = _FakeStore()
    store.search_all = lambda: [
        {"doc_id": "case-1-fail", "ts": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()},
        {"doc_id": "case-2", "ts": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()},
        {"doc_id": "case-3-fail", "ts": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()},
    ]
    store.delete_filter = lambda doc_id: deleted.append(doc_id)
    mem.purge_expired_cases(store=store)
    assert deleted == ["case-1-fail"]   # 只删超期失败案例,成功案例/未到期保留


def test_purge_disabled_when_retention_zero(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "case_retention_days", 0)
    store = _FakeStore()
    store.search_all = lambda: []
    mem.purge_expired_cases(store=store)   # 不报错即过
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_memory.py::test_purge_expired_cases_deletes_old_fail -v`
Expected: FAIL(`AttributeError: module 'app.agent.memory' has no attribute 'purge_expired_cases'`)

- [ ] **Step 3: 实现**

`memory.py` 加:

```python
def purge_expired_cases(store=None) -> None:
    """删除超过保留期的失败案例(case-*-fail);retention=0 不启用;异常降级。"""
    days = settings.case_retention_days
    if not days:
        return
    try:
        if store is None:
            store = _get_store()
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        for case in store.search_all():
            doc_id = case.get("doc_id") or ""
            if not doc_id.endswith("-fail"):
                continue
            try:
                ts = datetime.fromisoformat(case.get("ts", ""))
                if ts < cutoff:
                    store.delete_filter(doc_id)
            except (ValueError, TypeError):
                continue
    except Exception as exc:  # noqa: BLE001 淘汰失败不影响
        logger.warning("失败案例淘汰异常: %s", exc)
```

顶部确认 `from app.config import settings` 已 import。`_FakeStore` 需加 `search_all`(返回 list[dict])——在 test_memory.py 的现有 _FakeStore 上扩展。

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_memory.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/agent/memory.py ai-service/tests/test_memory.py
git commit -m "feat(hardening): 失败案例淘汰 — 超期 -fail 案例清理(默认关)"
```

---

### Task 5:评测触发 API(POST /api/evals/run)

**Files:**
- Modify: `ai-service/app/api/evals.py`
- Test: `ai-service/tests/test_evals_api.py`

**Interfaces:**
- Consumes: 后台跑 `eval_agent_report` 的 run 逻辑 + `write_report` 写库。
- Produces: `POST /api/evals/run`(body {scenario, rounds}):校验 → 202 + {status:"accepted"};后台线程执行。Task 6 的前端按钮消费。

- [ ] **Step 1: 写失败测试**

```python
# ai-service/tests/test_evals_api.py(追加)
def test_evals_run_validates_params():
    r = client.post("/api/evals/run", json={"scenario": "BAD_SCENARIO", "rounds": 9})
    assert r.status_code == 400


def test_evals_run_accepts(monkeypatch):
    started = []
    def fake_run(scenario, rounds):
        started.append((scenario, rounds))
    monkeypatch.setattr("app.api.evals._run_eval_background", fake_run)
    r = client.post("/api/evals/run", json={"scenario": "SCN-001", "rounds": 1})
    assert r.status_code == 202
    assert started == [("SCN-001", 1)]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_evals_api.py::test_evals_run_validates_params -v`
Expected: FAIL(`405` 或 404,路由不存在)

- [ ] **Step 3: 实现**

`evals.py` 加:

```python
import threading

_VALID_SCENARIOS = {"SCN-001", "SCN-002"}


def _run_eval_background(scenario: str, rounds: int) -> None:
    """后台跑一轮评测,结果自动写 eval_run。"""
    from scripts.eval_agent_report import main as eval_main  # 或抽取可调函数
    import argparse
    # 复用 eval_agent_report 的 run 逻辑(抽 run_evals(base, order, rounds, scenario) 更干净)
    ...


@router.post("/run")
def run_eval(payload: dict):
    scenario = payload.get("scenario", "")
    try:
        rounds = int(payload.get("rounds", 1))
    except (TypeError, ValueError):
        raise HTTPException(400, "rounds must be int")
    if scenario not in _VALID_SCENARIOS or not 1 <= rounds <= 5:
        raise HTTPException(400, "invalid scenario/rounds")
    t = threading.Thread(target=_run_eval_background, args=(scenario, rounds), daemon=True)
    t.start()
    return {"status": "accepted"}
```

(实现时需从 eval_agent_report 抽出可复用函数 `run_evals(base, order, rounds, scenario)` 供后台线程调用——避免 argparse 依赖。若抽函数,同步更新 eval_agent_report.py 与现有测试。)

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_evals_api.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/api/evals.py ai-service/tests/test_evals_api.py
git commit -m "feat(hardening): 评测触发 API — POST /api/evals/run(校验+后台跑)"
```

---

### Task 6:前端运行评测按钮

**Files:**
- Modify: `web/src/views/EvalDashboardView.vue`、`web/src/api/client.ts`
- Test: `web/src/views/EvalDashboardView.test.ts`

**Interfaces:**
- Consumes: `POST /api/evals/run`(Task 5)。
- Produces: EvalDashboardView 加"运行评测"按钮(scenario 下拉 + rounds 输入)→ POST → loading → 完成后刷新列表。无新接口。

- [ ] **Step 1: 写失败测试**

```ts
// web/src/views/EvalDashboardView.test.ts(追加)
it('点击运行评测触发 POST 并刷新', async () => {
  mocked.listEvals.mockResolvedValue([])
  mocked.runEval.mockResolvedValue({ status: 'accepted' })
  const wrapper = mount(EvalDashboardView, { global: { plugins: [ElementPlus] } })
  await flushPromises()
  await wrapper.find('[data-testid="run-eval-btn"]').trigger('click')
  await flushPromises()
  expect(mocked.runEval).toHaveBeenCalled()
  expect(mocked.listEvals).toHaveBeenCalledTimes(2)   // 初始 + 刷新
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd web && npx vitest run src/views/EvalDashboardView.test.ts`
Expected: FAIL(按钮不存在)

- [ ] **Step 3: 实现**

`client.ts` 加:

```ts
export function runEval(input: { scenario: string; rounds: number }): Promise<{ status: string }> {
  return request('/api/evals/run', { method: 'POST', body: JSON.stringify(input) })
}
```

`EvalDashboardView.vue` 统计卡区域加:

```html
<el-card shadow="never">
  <template #header>运行评测</template>
  <el-select v-model="runScenario" data-testid="run-scenario" style="width: 140px">
    <el-option label="SCN-001" value="SCN-001" />
    <el-option label="SCN-002" value="SCN-002" />
  </el-select>
  <el-input-number v-model="runRounds" :min="1" :max="5" data-testid="run-rounds" />
  <el-button type="primary" data-testid="run-eval-btn" :loading="running" @click="doRunEval">
    运行评测
  </el-button>
  <span v-if="running" style="margin-left: 8px; color: #e6a23c">真实模型会耗额度</span>
</el-card>
```

script:

```ts
const runScenario = ref('SCN-001')
const runRounds = ref(1)
const running = ref(false)

async function doRunEval() {
  running.value = true
  try {
    await runEval({ scenario: runScenario.value, rounds: runRounds.value })
    ElMessage.success('评测已提交,结果生成后自动刷新')
    evals.value = await listEvals()
  } finally {
    running.value = false
  }
}
```

测试 mock `@/api/client` 需加 `runEval: vi.fn()`。

- [ ] **Step 4: 运行确认通过**

Run: `cd web && npx vitest run src/views/EvalDashboardView.test.ts`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add web/src/views/EvalDashboardView.vue web/src/api/client.ts web/src/views/EvalDashboardView.test.ts
git commit -m "feat(hardening): 前端运行评测按钮(POST + loading + 自动刷新)"
```

---

### Task 7:整体回归 + 冒烟 + 推送

**Files:**
- 全部改动文件。

**Interfaces:**
- 无新接口;验证 Task 1-6 集成。

- [ ] **Step 1: 后端全量测试**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/ -q`
Expected: 全部 PASS(原 422 + 新增,无回归)。

- [ ] **Step 2: 前端全量测试 + build**

```bash
cd web && npx vitest run
cd web && npx vue-tsc --noEmit && npx vite build
```

Expected: 全部 PASS + build 成功。

- [ ] **Step 3: 冒烟(默认关验证)**

```bash
cd ai-service && .venv/Scripts/python.exe -c "
from app.config import settings
from app.agent.model_scorer import ModelScorer
sc = ModelScorer()
for _ in range(10): sc.update('select_tool','m',{'success':True,'latency_ms':100,'cost':0.001})
print('epsilon default:', settings.routing_epsilon)
print('best(ε=0):', sc.best('select_tool',['m'],epsilon=0.0))
print('cost_budget default:', settings.cost_budget)
print('retention default:', settings.case_retention_days)
"
```

Expected: 默认值正确 + best 选唯一候选(默认行为不变)。

- [ ] **Step 4: 提交(如有修复)+ 推送**

```bash
git add -A && git commit -m "fix(hardening): 回归修复"
git push origin main
```

(注意:GitHub 网络间歇不可用——若失败记录待推提交数,稍后重试。)
