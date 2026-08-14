# V1.12 动态路由学习(窗口滚动评分) 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 TraceMind Agent 的路由根据历史调用表现自动调整——每个 (node, model) 组合按成功率/latency/成本加权滚动评分,`route(node)` 在候选模型列表里选评分最高者;模型表现漂移时路由自动跟随。

**Architecture:** 新增 `ModelScorer`(内存滑动窗口 + 加权评分),`route()` 增强为"动态路由优先、静态回落";`_audit_model_call` 审计落库后触发 `scorer.update()`。默认开关关闭,开启后与 V1.11 完全兼容。

**Tech Stack:** Python 3.12 / collections.deque / SQLAlchemy(model_call 表,复用)。

## Global Constraints

- 默认 `TRACEMIND_DYNAMIC_ROUTING=false`;开启且候选配置了才走动态路由。
- 候选模型窗口数据 < 5 次(冷启动)→ 返回配置默认模型(第一候选),不瞎猜。
- 候选未配置 / 动态路由关闭 / scorer 异常 → 回落 V1.11 静态 `route()`(零风险)。
- 窗口默认 N=20,满则淘汰最旧(滚动非累计)。
- 评分权重 `w1=0.6(成功率), w2=0.25(时延), w3=0.15(成本)`,可配置 `TRACEMIND_ROUTING_WEIGHTS="0.6,0.25,0.15"`。
- `update` 只在审计落库后触发一次(基于最终结果:结构化输出有效 → 成功;否则失败)。
- fallback(容灾)与成本统计保持不变;动态路由选出的模型若 429/5xx 仍切 fallback 重试。
- 不做多臂老虎机/UCB、不做评分前端可视化、不引入新依赖、不改前端。
- 沿用 V1.6 决定:不做 CI,验证手动(`cd ai-service && .venv/Scripts/pytest.exe tests/ -q`)。
- 候选模型须来自已实测通过(SO+TC)的集合:qwen3.7-flash / qwen3.8-max / qwen3.7-max / deepseek-v4-flash-0731。

## File Structure

- `ai-service/app/config/settings.py`(Modify):加 dynamic_routing/routing_window/routing_weights/*_candidates 字段。
- `ai-service/app/agent/model_scorer.py`(Create):`ModelScorer`(窗口 + 评分)。
- `ai-service/app/agent/model_router.py`(Modify):`route` 增强(动态优先 + 静态回落)。
- `ai-service/app/agent/llm.py`(Modify):`_audit_model_call` 落库后触发 `scorer.update()`。
- `ai-service/tests/test_model_scorer.py`(Create):评分/窗口/选优/冷启动测试。
- `ai-service/tests/test_model_router.py`(Modify):动态路由回落测试。

---

### Task 1:settings 动态路由配置

**Files:**
- Modify: `ai-service/app/config/settings.py`
- Test: `ai-service/tests/test_model_router.py`(验证新字段存在)

**Interfaces:**
- Produces: `settings.dynamic_routing: bool = False`、`settings.routing_window: int = 20`、`settings.routing_weights: str = "0.6,0.25,0.15"`、`settings.select_tool_candidates/hypothesize_candidates/reflect_candidates/report_candidates: str = ""`。Task 2-4 依赖。

- [ ] **Step 1: 写失败测试**

```python
# ai-service/tests/test_model_router.py(追加)
def test_settings_has_dynamic_routing_fields():
    from app.config import settings
    assert hasattr(settings, "dynamic_routing")
    assert settings.dynamic_routing is False          # 默认关
    assert settings.routing_window == 20
    assert settings.routing_weights == "0.6,0.25,0.15"
    assert settings.select_tool_candidates == ""
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_model_router.py::test_settings_has_dynamic_routing_fields -v`
Expected: FAIL(`AttributeError: 'Settings' object has no attribute 'dynamic_routing'`)

- [ ] **Step 3: 实现**

`settings.py` 的 V1.11 路由区块后加:

```python
    # ---- V1.12 动态路由:窗口滚动评分(默认关;开启才启用)----
    dynamic_routing: bool = False
    routing_window: int = 20                 # 滑动窗口大小
    routing_weights: str = "0.6,0.25,0.15"   # 成功率/时延/成本权重
    select_tool_candidates: str = ""         # 候选模型,逗号分隔,如 "qwen3.7-flash,qwen3.8-max"
    hypothesize_candidates: str = ""
    reflect_candidates: str = ""
    report_candidates: str = ""
```

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_model_router.py -v`
Expected: PASS(含既有 4 个 + 新 1 个)

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/config/settings.py ai-service/tests/test_model_router.py
git commit -m "feat(router): V1.12 动态路由配置字段(默认关,零风险)"
```

---

### Task 2:ModelScorer(窗口 + 评分)

**Files:**
- Create: `ai-service/app/agent/model_scorer.py`
- Test: `ai-service/tests/test_model_scorer.py`

**Interfaces:**
- Produces: `ModelScorer(window=20, weights=(0.6,0.25,0.15))`;`update(node, model, outcome: dict)`;`best(node, candidates) -> str | None`;`MIN_SAMPLES = 5`。Task 3 的 route 依赖 best;Task 4 的审计依赖 update。

- [ ] **Step 1: 写失败测试**

```python
# ai-service/tests/test_model_scorer.py
import pytest
from app.agent.model_scorer import ModelScorer, MIN_SAMPLES


def _out(success=True, latency_ms=100, cost=0.001):
    return {"success": success, "latency_ms": latency_ms, "cost": cost}


def test_score_prefers_high_success():
    sc = ModelScorer()
    for _ in range(10):
        sc.update("select_tool", "flash", _out(True, 100, 0.001))      # 全成功
        sc.update("select_tool", "max", _out(False, 100, 0.01))        # 全失败
    assert sc.best("select_tool", ["flash", "max"]) == "flash"


def test_score_balances_latency():
    sc = ModelScorer()
    for _ in range(10):
        sc.update("select_tool", "fast", _out(True, 50, 0.001))
        sc.update("select_tool", "slow", _out(True, 500, 0.001))
    assert sc.best("select_tool", ["fast", "slow"]) == "fast"


def test_window_rolls_oldest_out():
    sc = ModelScorer(window=5)
    for i in range(8):
        sc.update("hypothesize", "m", _out(True if i < 5 else False, 100, 0.001))
    # 窗口 5:前 5 条成功(旧)已被淘汰,新 3 条失败 → success_rate = 0
    assert sc.best("hypothesize", ["m"]) == "m"          # 唯一候选仍返回
    stats = sc._windows[("hypothesize", "m")]
    assert len(stats) == 5                                # 窗口封顶
    assert all(not s["success"] for s in stats)           # 旧的 5 条成功被滚出


def test_cold_start_returns_none():
    sc = ModelScorer()
    sc.update("select_tool", "flash", _out(True, 100, 0.001))   # 仅 1 次 < MIN_SAMPLES
    assert sc.best("select_tool", ["flash", "max"]) is None     # 冷启动不瞎猜


def test_best_unknown_node_none():
    sc = ModelScorer()
    assert sc.best("unknown_node", ["a"]) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_model_scorer.py -v`
Expected: FAIL(`ImportError: cannot import name 'ModelScorer'`)

- [ ] **Step 3: 实现**

```python
"""V1.12 动态路由:ModelScorer 按 (node, model) 滑动窗口维护加权评分。"""
from collections import deque

MIN_SAMPLES = 5  # 窗口数据少于该值视为冷启动,返回 None


class ModelScorer:
    def __init__(self, window: int = 20,
                 weights: tuple[float, float, float] = (0.6, 0.25, 0.15)):
        self.window = window
        self.w1, self.w2, self.w3 = weights
        self._windows: dict[tuple[str, str], deque] = {}

    def update(self, node: str, model: str, outcome: dict) -> None:
        key = (node, model)
        q = self._windows.setdefault(key, deque(maxlen=self.window))
        q.append({"success": bool(outcome.get("success")),
                  "latency_ms": outcome.get("latency_ms") or 0,
                  "cost": outcome.get("cost") or 0.0})

    def best(self, node: str, candidates: list[str]) -> str | None:
        """候选里选窗口评分最高者;数据不足(< MIN_SAMPLES)返回 None(调用方回落默认)。"""
        scored = []
        for m in candidates:
            q = self._windows.get((node, m))
            if q is None or len(q) < MIN_SAMPLES:
                continue
            scored.append((self._score(q), m))
        if not scored:
            return None
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    def _score(self, q: deque) -> float:
        n = len(q)
        success = sum(1 for s in q if s["success"])
        success_rate = success / n
        p95 = sorted(s["latency_ms"] for s in q)[int(n * 0.95) - 1] if n else 0
        latency_norm = 0.0
        if p95 > 0:
            latency_norm = min(1.0, max(0.0, sum(s["latency_ms"] for s in q) / n / p95))
        max_cost = max((s["cost"] for s in q), default=0.0)
        cost_norm = 0.0
        if max_cost > 0:
            cost_norm = min(1.0, max(0.0, sum(s["cost"] for s in q) / n / max_cost))
        return (self.w1 * success_rate
                + self.w2 * (1 - latency_norm)
                + self.w3 * (1 - cost_norm))
```

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_model_scorer.py -v`
Expected: PASS(5 个用例)

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/agent/model_scorer.py ai-service/tests/test_model_scorer.py
git commit -m "feat(scorer): ModelScorer — 滑动窗口加权评分(成功率/时延/成本)"
```

---

### Task 3:route 增强(动态优先 + 静态回落)

**Files:**
- Modify: `ai-service/app/agent/model_router.py`
- Test: `ai-service/tests/test_model_router.py`

**Interfaces:**
- Consumes: `ModelScorer.best`(Task 2)、`settings.dynamic_routing/*_candidates`(Task 1)。
- Produces: `route(node)` 在动态路由开启且有候选、数据充足时返回评分最高模型;否则回落 V1.11 静态行为。Task 4 的审计更新闭环依赖 route 使用动态结果。

- [ ] **Step 1: 写失败测试**

```python
# ai-service/tests/test_model_router.py(追加)
def test_route_dynamic_picks_best_candidate(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "dynamic_routing", True)
    monkeypatch.setattr(settings, "select_tool_candidates", "qwen3.7-flash,qwen3.8-max")
    from app.agent import model_router, model_scorer
    sc = model_scorer.ModelScorer()
    for _ in range(10):
        sc.update("select_tool", "qwen3.7-flash", {"success": True, "latency_ms": 50, "cost": 0.001})
        sc.update("select_tool", "qwen3.8-max", {"success": False, "latency_ms": 100, "cost": 0.01})
    monkeypatch.setattr(model_router, "scorer", sc)
    assert model_router.route("select_tool") == "qwen3.7-flash"


def test_route_dynamic_disabled_falls_back_static(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "dynamic_routing", False)
    monkeypatch.setattr(settings, "select_tool_model", "qwen3.7-flash")
    from app.agent import model_router
    assert model_router.route("select_tool") == "qwen3.7-flash"


def test_route_dynamic_cold_start_falls_back(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "dynamic_routing", True)
    monkeypatch.setattr(settings, "select_tool_candidates", "qwen3.7-flash,qwen3.8-max")
    monkeypatch.setattr(settings, "select_tool_model", "qwen3.7-flash")
    from app.agent import model_router
    assert model_router.route("select_tool") == "qwen3.7-flash"   # 数据不足 → 默认
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_model_router.py::test_route_dynamic_picks_best_candidate tests/test_model_router.py::test_route_dynamic_disabled_falls_back_static tests/test_model_router.py::test_route_dynamic_cold_start_falls_back -v`
Expected: 失败(动态路由未实现,返回静态)

- [ ] **Step 3: 实现**

`model_router.py` 改为:

```python
"""V1.12 多模型路由:动态评分优先,静态配置回落。"""
from app.agent.model_scorer import ModelScorer
from app.config import settings

NODE_MODEL_KEY = {
    "hypothesize": "hypothesize_model",
    "select_tool": "select_tool_model",
    "reflect": "reflect_model",
    "write_report": "report_model",
}

NODE_CANDIDATES_KEY = {
    "hypothesize": "hypothesize_candidates",
    "select_tool": "select_tool_candidates",
    "reflect": "reflect_candidates",
    "write_report": "report_candidates",
}

scorer = ModelScorer()   # 模块级单例(进程内共享)


def _candidates(node: str) -> list[str]:
    key = NODE_CANDIDATES_KEY.get(node)
    if key is None:
        return []
    raw = getattr(settings, key, "") or ""
    return [m.strip() for m in raw.split(",") if m.strip()]


def _static_route(node: str) -> str | None:
    key = NODE_MODEL_KEY.get(node)
    if key is None:
        return None
    return getattr(settings, key, "") or None


def route(node: str) -> str | None:
    """动态路由:候选里选评分最高者;未启用/无候选/数据不足 → 回落静态配置。"""
    if settings.dynamic_routing:
        candidates = _candidates(node)
        if candidates:
            chosen = scorer.best(node, candidates)
            if chosen:
                return chosen
    return _static_route(node)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_model_router.py -v`
Expected: PASS(既有 4 + 新 3)

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/agent/model_router.py ai-service/tests/test_model_router.py
git commit -m "feat(router): route 动态优先 — 候选评分选优,冷启动/未启用回落静态"
```

---

### Task 4:审计联动(update 触发)

**Files:**
- Modify: `ai-service/app/agent/llm.py`
- Test: `ai-service/tests/test_llm_audit.py`(追加)

**Interfaces:**
- Consumes: `ModelScorer.update`(Task 2)、`_audit_model_call` 已有的 node/model/latency/tokens/status。
- Produces: 审计落库成功后调用 `scorer.update(node, model, {success, latency_ms, cost})`;`cost` 用 `MODEL_PRICE_PER_M` 估算。动态路由闭环:调用 → 审计 → 评分更新 → 下次 route 用新评分。

- [ ] **Step 1: 写失败测试**

```python
# ai-service/tests/test_llm_audit.py(追加)
def test_audit_updates_scorer(monkeypatch):
    from app.agent import model_router
    from app.agent.model_scorer import ModelScorer
    sc = ModelScorer()
    monkeypatch.setattr(model_router, "scorer", sc)
    calls = []
    monkeypatch.setattr(model_call_repo, "insert", lambda **kw: calls.append(kw))
    _silence_retrieval(monkeypatch)
    l = _mk_llm(_FakeClient(content='{"hypotheses":[{"description":"缺联合索引"}]}'))
    l.hypothesize({"incident_id": 1, "run_id": 2, "description": "慢查询"})
    # 审计后 scorer 应有一条 (hypothesize, model) 记录
    keys = [k for k in sc._windows if k[0] == "hypothesize"]
    assert keys, "scorer 应收到 hypothesize 的 update"
    assert len(sc._windows[keys[0]]) == 1
    assert sc._windows[keys[0]][0]["success"] is True
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_llm_audit.py::test_audit_updates_scorer -v`
Expected: FAIL(`assert keys` — scorer 未收到 update)

- [ ] **Step 3: 实现**

`llm.py` 的 `_audit_model_call` 内,`model_call_repo.insert(...)` 成功(`except` 之外的正常路径)后加:

```python
        # V1.12 动态路由:审计落库后更新评分(基于最终结果)
        from app.agent.model_router import scorer
        from app.agent.cost import MODEL_PRICE_PER_M
        try:
            _m = model or settings.chat_model_resolved or "unknown"
            unit = MODEL_PRICE_PER_M.get(_m)
            cost = unit * ((input_tokens or 0) + (output_tokens or 0)) / 1_000_000 if unit else 0.0
            scorer.update(
                node, _m,
                {"success": structured_output_valid, "latency_ms": latency_ms, "cost": cost})
        except Exception:  # noqa: BLE001 评分失败不影响主流程
            logger.warning("动态路由评分更新失败", exc_info=True)
```

注意:放 `try/except Exception` 内(与 insert 同一 try 的尾部,或新 try)——确保评分失败不阻断主流程。若 insert 本身失败,评分也应跳过(没有数据依据)。

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_llm_audit.py -v`
Expected: PASS(含既有 + 新用例)

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/agent/llm.py ai-service/tests/test_llm_audit.py
git commit -m "feat(scorer): 审计落库后更新动态路由评分(基于最终结果)"
```

---

### Task 5:整体回归 + 冒烟

**Files:**
- 全部改动文件。

**Interfaces:**
- 无新接口;验证 Task 1-4 集成。

- [ ] **Step 1: 后端全量测试**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/ -q`
Expected: 全部 PASS(原 405 + 新增,无回归)。若失败,逐个修复:重点检查 `model_router` 模块级 `scorer` 单例在测试间状态泄漏(测试需 monkeypatch 或重置)、`_audit_model_call` 的 update 是否破坏既有 mock。

- [ ] **Step 2: 冒烟动态路由**

```bash
cd ai-service && .venv/Scripts/python.exe -c "
from app.config import settings
settings.dynamic_routing = True
settings.select_tool_candidates = 'qwen3.7-flash,qwen3.8-max'
from app.agent import model_router
model_router.scorer.update('select_tool','qwen3.7-flash',{'success':True,'latency_ms':50,'cost':0.001})
for _ in range(5): model_router.scorer.update('select_tool','qwen3.8-max',{'success':False,'latency_ms':100,'cost':0.01})
print('dynamic route(select_tool):', model_router.route('select_tool'))
settings.dynamic_routing = False
print('static fallback:', model_router.route('select_tool'))
"
```

Expected: `dynamic route(select_tool): qwen3.7-flash`(评分选优)+ `static fallback: None`(未配置静态回落 None)

- [ ] **Step 3: 提交(如有修复)**

```bash
git add -A && git commit -m "fix(router): 回归修复"
```

---

### Task 6:VM 真实模型验收

**Files:**
- 无代码改动;部署验证。

**Interfaces:**
- 依赖 Task 1-5 全部代码。

- [ ] **Step 1: 同步代码到 VM 并重建**

```bash
cd ai-service && tar czf ../.reasonix/tmp/ai_v112.tar.gz app
cd .. && python .reasonix/tools/vm_ssh.py put .reasonix/tmp/ai_v112.tar.gz tracemind/ai_v112.tar.gz
timeout 60 python .reasonix/tools/vm_ssh.py run "cd ~/tracemind/ai-service && rm -rf app && tar xzf ../ai_v112.tar.gz && nohup bash -c 'cd ~/tracemind/ai-service && DOCKER_BUILDKIT=0 docker build -t tracemind-ai-service:latest --target ai-runtime . > /tmp/b-ai-v112.log 2>&1; echo EXIT=\$? >> /tmp/b-ai-v112.log' >/dev/null 2>&1 & echo 重建中"
```

轮询日志直到 `EXIT=0`(V1.9/V1.10/V1.11 踩坑:后台构建 + 轮询)。

- [ ] **Step 2: 环境自查(必做,V1.10 教训)**

```bash
timeout 60 python .reasonix/tools/vm_ssh.py run "cd ~/tracemind && docker compose up -d 2>&1 | tail -1 && docker compose --profile observability-ui up -d 2>&1 | tail -1 && curl -s 'http://127.0.0.1:6333/collections' | python3 -c 'import json,sys; print([c[\"name\"] for c in json.load(sys.stdin)[\"result\"][\"collections\"]])'"
```

Expected: 服务起来;collections 含 `tracemind_runbook_current`。若缺:重建 runbook 索引(V1.10 记忆)。

- [ ] **Step 3: 配置动态路由 + 切真实模型 + 启动**

```bash
timeout 60 python .reasonix/tools/vm_ssh.py run "cd ~/tracemind && \
  grep -q TRACEMIND_DYNAMIC_ROUTING .env.vm || echo '
TRACEMIND_DYNAMIC_ROUTING=true
TRACEMIND_ROUTING_WINDOW=20
TRACEMIND_ROUTING_WEIGHTS=0.6,0.25,0.15
TRACEMIND_SELECT_TOOL_CANDIDATES=qwen3.7-flash,qwen3.8-max
TRACEMIND_HYPOTHESIZE_CANDIDATES=qwen3.7-max,qwen3.8-max
TRACEMIND_FALLBACK_MODEL=deepseek-v4-flash-0731' >> .env.vm; \
  sed -i 's/TRACEMIND_LLM_MODE: fake/TRACEMIND_LLM_MODE: real_strict/' compose.yml && \
  sed -i 's/TRACEMIND_RAG_MODE: off/TRACEMIND_RAG_MODE: required/' compose.yml && \
  docker compose up -d --no-build ai-service 2>&1 | tail -1"
```

- [ ] **Step 4: 跑 SCN 验收(真实模型,耗百炼额度)**

```bash
timeout 280 python scripts/verify-m14.py --base http://192.168.88.10:8000 --order http://192.168.88.10:8081 --rounds 1 --scenario SCN-001
```

Expected: 至少一轮 recovered。**若遇 429/额度错误:立即停止并告知用户(见 tracemind-real-model-quota)。**

- [ ] **Step 5: 验证动态路由生效**

通过 VM 查 model_call 表,确认 select_tool 在候选间选择:

```bash
docker exec tracemind-ai python -c "
from sqlalchemy import create_engine, text; import os
e = create_engine(os.environ['TRACEMIND_CONTROL_DB_URL'])
with e.connect() as c:
    rows = c.execute(text(\"SELECT node, model, COUNT(*) FROM model_call WHERE node='select_tool' AND incident_id=(SELECT MAX(id) FROM incident) GROUP BY node, model\")).fetchall()
    for r in rows: print(r)
"
```

Expected: select_tool 用候选中的模型(flash 或 max)。冷启动(<5 次)时可能只用默认第一个候选——记录说明即可。

- [ ] **Step 6: 恢复 VM 默认(fake/off)+ 清理 + 推送**

```bash
timeout 60 python .reasonix/tools/vm_ssh.py run "cd ~/tracemind && sed -i 's/TRACEMIND_LLM_MODE: real_strict/TRACEMIND_LLM_MODE: fake/; s/TRACEMIND_RAG_MODE: required/TRACEMIND_RAG_MODE: off/' compose.yml && docker compose up -d --no-build ai-service 2>&1 | tail -1"
rm -f .reasonix/tmp/ai_v112.tar.gz
git push origin main
```
