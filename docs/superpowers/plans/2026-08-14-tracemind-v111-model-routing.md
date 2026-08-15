# V1.11 多模型路由 + 成本统计 + 容灾降级 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 TraceMind Agent 按节点任务难度路由模型(强推理节点用 qwen3.8-max、高频/生成节点用 qwen3.7-flash),基于现有 model_call 审计表聚合成本账单,并在主模型 429/5xx/超时失败时自动切换异厂商 fallback 模型重试。

**Architecture:** 新增 `ModelRouter`(纯配置路由,未配置回落默认零风险)与 `CostTracker`(聚合 model_call 表的 model/tokens/成本);`LLMClient.chat` 在 3 次退避重试仍失败后插入 fallback 重试(仅对 RETRY_STATUS 类错误);节点方法经 `_chat_json_with_usage` 透传 `model=route(node)`。

**Tech Stack:** Python 3.12 / SQLAlchemy / httpx(LLM 客户端)/ bailian compatible-mode(OpenAI 兼容)。

## Global Constraints

- 路由零配置回落:未配置节点 / 全空配置 → 用 `chat_model_resolved`(与现状完全一致)。
- 路由模型映射:`hypothesize→hypothesize_model`、`select_tool→select_tool_model`、`reflect→reflect_model`、`write_report→report_model`。
- fallback 仅对 RETRY_STATUS({429,500,502,503,504})触发;NO_RETRY_STATUS(400/401/403/404)不触发。
- fallback 成功 → model_call 记 `degraded=True` + 实际模型;fallback 也失败 → 返回 None/抛 ModelDegradedError(上层转 needs_human,与现状一致)。
- fallback 与主模型相同 → 不重复重试;`fallback_model` 未配置 → 不启用 fallback。
- 单价未配置的模型 → 成本记 0,不报错、不阻断。
- 不做成本预算告警、不做动态路由学习、不引入新依赖、不改前端主流程。
- 沿用 V1.6 决定:不做 CI,验证手动(`cd ai-service && .venv/Scripts/pytest.exe tests/ -q`)。
- 模型名(已实测 SO+TC 通过):qwen3.8-max / qwen3.7-flash / deepseek-v4-flash-0731。

## File Structure

- `ai-service/app/config/settings.py`(Modify):加 5 个路由/fallback 配置字段。
- `ai-service/app/agent/model_router.py`(Create):`route(node)` 路由函数 + `NODE_MODEL_KEY` 映射。
- `ai-service/app/agent/cost.py`(Create):`MODEL_PRICE_PER_M` 单价表 + `aggregate_model_costs(calls)`。
- `ai-service/app/agent/llm_client.py`(Modify):`chat` 3 次重试失败后 fallback 重试(返回 `(result, actual_model)` 或内部记录)。
- `ai-service/app/agent/llm.py`(Modify):`_chat_json_with_usage` 加 `model` 透传;节点方法按路由传 model。
- `ai-service/tests/test_model_router.py`(Create):路由测试。
- `ai-service/tests/test_cost.py`(Create):成本聚合测试。
- `ai-service/tests/test_llm_client.py`(Modify):fallback 容灾测试。

---

### Task 1:ModelRouter(配置 + 路由函数)

**Files:**
- Modify: `ai-service/app/config/settings.py`
- Create: `ai-service/app/agent/model_router.py`
- Test: `ai-service/tests/test_model_router.py`

**Interfaces:**
- Produces: `route(node: str) -> str | None`;`NODE_MODEL_KEY = {"hypothesize": "hypothesize_model", "select_tool": "select_tool_model", "reflect": "reflect_model", "write_report": "report_model"}`。Task 2 的节点注入依赖它。

- [ ] **Step 1: 写失败测试**

```python
# ai-service/tests/test_model_router.py
from app.agent import model_router
from app.config import settings


def test_route_configured_node(monkeypatch):
    monkeypatch.setattr(settings, "select_tool_model", "qwen3.7-flash")
    assert model_router.route("select_tool") == "qwen3.7-flash"


def test_route_unconfigured_node_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "hypothesize_model", "")
    assert model_router.route("hypothesize") is None


def test_route_unknown_node_returns_none(monkeypatch):
    assert model_router.route("unknown_node") is None


def test_route_all_empty_returns_none(monkeypatch):
    for k in model_router.NODE_MODEL_KEY.values():
        monkeypatch.setattr(settings, k, "")
    assert model_router.route("select_tool") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_model_router.py -v`
Expected: FAIL(`ImportError: cannot import name 'model_router'`)

- [ ] **Step 3: 实现**

`settings.py` 的 Chat Provider 区块加字段(对齐现有风格,`env_prefix="TRACEMIND_"` 自动映射):

```python
    # ---- V1.11 多模型路由:按节点选模型(空 → 回落 chat_model_resolved)----
    hypothesize_model: str = ""
    select_tool_model: str = ""
    reflect_model: str = ""
    report_model: str = ""
    fallback_model: str = ""            # 容灾备用;空 → 不启用 fallback
```

新建 `app/agent/model_router.py`:

```python
"""V1.11 多模型路由:按节点任务难度选模型(纯配置驱动)。"""
from app.config import settings

NODE_MODEL_KEY = {
    "hypothesize": "hypothesize_model",
    "select_tool": "select_tool_model",
    "reflect": "reflect_model",
    "write_report": "report_model",
}


def route(node: str) -> str | None:
    """返回该节点应使用的模型;未配置/未知节点返回 None(调用方回落默认)。"""
    key = NODE_MODEL_KEY.get(node)
    if key is None:
        return None
    return getattr(settings, key, "") or None
```

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_model_router.py -v`
Expected: PASS(4 个用例)

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/config/settings.py ai-service/app/agent/model_router.py ai-service/tests/test_model_router.py
git commit -m "feat(router): ModelRouter — 按节点路由模型,零配置回落默认"
```

---

### Task 2:节点注入路由模型

**Files:**
- Modify: `ai-service/app/agent/llm.py`
- Test: `ai-service/tests/test_llm.py`(或现有测试文件,先确认路径)

**Interfaces:**
- Consumes: `route(node) -> str | None`(Task 1)。
- Produces: `_chat_json_with_usage(messages, max_tokens, model=None)`;`hypothesize/select_tool/reflect/write_report` 调用时传 `model=route("hypothesize")` 等。model_call 审计的 `model` 字段自动记录实际模型(供 Task 3 成本聚合)。

- [ ] **Step 1: 写失败测试**

```python
# 在现有 LLM 测试文件追加(先读文件确认 import 风格)
def test_hypothesize_uses_routed_model(monkeypatch):
    from app.agent import model_router
    from app.config import settings
    monkeypatch.setattr(settings, "hypothesize_model", "qwen3.8-max")
    captured = {}
    class _Client:
        def chat_json_with_usage(self, messages, max_tokens=600, model=None):
            captured["model"] = model
            return {"hypotheses": [{"description": "h"}]}, {"input_tokens": 10, "output_tokens": 5}, "stop"
    from app.agent.llm import OpenAICompatibleLLM
    llm = OpenAICompatibleLLM(client=_Client(), strict=True)
    llm.hypothesize({"description": "库存慢", "incident_id": 1, "run_id": 1})
    assert captured["model"] == "qwen3.8-max"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && .venv/Scripts/pytest.exe <testfile>::test_hypothesize_uses_routed_model -v`
Expected: FAIL(`assert captured["model"] == "qwen3.8-max"` — 当前为 None)

- [ ] **Step 3: 实现**

`llm.py` 的 `_chat_json_with_usage` 加 model 透传:

```python
    def _chat_json_with_usage(self, messages: list[dict], max_tokens: int = 600,
                              model: str | None = None):
        """调 chat_json_with_usage(client 层),返回 (data, usage, finish_reason)。"""
        return self.client.chat_json_with_usage(messages, max_tokens=max_tokens, model=model)
```

`hypothesize` 方法内,`self._chat_json_with_usage([...])` 改为:

```python
            data, usage, finish = self._chat_json_with_usage(
                [{"role": "user", "content": prompt}], model=route("hypothesize"))
```

同理 `select_tool`(line ~257 的 `self.client.chat`)→ `model=route("select_tool")`;`write_report`(line ~304)→ `model=route("write_report")`;`reflect`(V1.10 新增,line ~351)→ `model=route("reflect")`。文件顶部 import:`from app.agent.model_router import route`。

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && .venv/Scripts/pytest.exe <testfile> -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/agent/llm.py <testfile>
git commit -m "feat(router): 节点注入路由模型 — hypothesize/select_tool/reflect/report 按路由选模型"
```

---

### Task 3:CostTracker(成本聚合)

**Files:**
- Create: `ai-service/app/agent/cost.py`
- Test: `ai-service/tests/test_cost.py`

**Interfaces:**
- Consumes: `model_call` 表查询结果(list[dict],字段含 `model`/`input_tokens`/`output_tokens`)。
- Produces: `aggregate_model_costs(calls: list[dict]) -> dict`;`MODEL_PRICE_PER_M: dict[str, float]`。Task 4/5 的展示与验收依赖它。

- [ ] **Step 1: 写失败测试**

```python
# ai-service/tests/test_cost.py
from app.agent import cost


def test_aggregate_by_model():
    calls = [
        {"model": "qwen3.8-max", "input_tokens": 1000, "output_tokens": 200},
        {"model": "qwen3.8-max", "input_tokens": 500, "output_tokens": 100},
        {"model": "qwen3.7-flash", "input_tokens": 2000, "output_tokens": 300},
    ]
    out = cost.aggregate_model_costs(calls)
    assert out["qwen3.8-max"]["calls"] == 2
    assert out["qwen3.8-max"]["input_tokens"] == 1500
    assert out["qwen3.8-max"]["cost"] == pytest.approx(
        cost.MODEL_PRICE_PER_M["qwen3.8-max"] * 1700 / 1_000_000, rel=1e-3)


def test_aggregate_unknown_model_cost_zero():
    out = cost.aggregate_model_costs([{"model": "some-unknown", "input_tokens": 100, "output_tokens": 10}])
    assert out["some-unknown"]["cost"] == 0


def test_aggregate_empty():
    assert cost.aggregate_model_costs([]) == {}
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_cost.py -v`
Expected: FAIL(`ImportError: cannot import name 'cost'`)

- [ ] **Step 3: 实现**

```python
"""V1.11 成本统计:按模型聚合 model_call 的 token 与估算成本。"""
# 每百万 token 单价(元);按百炼公开价配置,可覆盖。未配置模型成本记 0。
MODEL_PRICE_PER_M = {
    "qwen3.8-max": 20.0,
    "qwen3.7-max": 20.0,
    "qwen3.7-flash": 0.5,
    "deepseek-v4-flash-0731": 1.0,
}


def aggregate_model_costs(calls: list[dict]) -> dict:
    """按模型聚合:调用次数 / input_tokens / output_tokens / 估算成本(元)。
    calls: model_call 查询结果(list[dict],含 model/input_tokens/output_tokens)。"""
    out: dict[str, dict] = {}
    for c in calls:
        m = c.get("model") or "unknown"
        item = out.setdefault(m, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0})
        item["calls"] += 1
        item["input_tokens"] += c.get("input_tokens") or 0
        item["output_tokens"] += c.get("output_tokens") or 0
    for m, item in out.items():
        unit = MODEL_PRICE_PER_M.get(m)
        if unit:
            item["cost"] = round(unit * (item["input_tokens"] + item["output_tokens"]) / 1_000_000, 6)
    return out
```

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_cost.py -v`
Expected: PASS(3 个用例)

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/agent/cost.py ai-service/tests/test_cost.py
git commit -m "feat(cost): CostTracker — 按模型聚合 token/成本,单价可配置"
```

---

### Task 4:LLMClient 容灾 fallback

**Files:**
- Modify: `ai-service/app/agent/llm_client.py`
- Test: `ai-service/tests/test_llm_client.py`(先读文件确认现有测试结构)

**Interfaces:**
- Consumes: `settings.fallback_model`(Task 1 配置)。
- Produces: `LLMClient.chat` 在 3 次退避重试仍失败后,若 `fallback_model` 已配置且与主模型不同,用 fallback 模型重试 1 次;成功返回 `ChatResult`(model 为 fallback),仍失败返回 None。Task 5 的 model_call 审计需要知道是否用了 fallback。

- [ ] **Step 1: 写失败测试**

先读 `tests/test_llm_client.py` 确认现有 mock 方式,再追加:

```python
def test_chat_fallback_on_retry_exhausted(monkeypatch):
    # 主模型 3 次 429 → fallback 成功
    calls = []
    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json["model"])
        if len(calls) <= 3:
            resp = httpx.Response(429, request=httpx.Request("POST", url))
            return resp
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok", "tool_calls": []}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", fake_post)
    client = LLMClient(base_url="http://llm", api_key="k", model="qwen3.8-max")
    monkeypatch.setattr(settings, "fallback_model", "deepseek-v4-flash-0731")
    r = client.chat([{"role": "user", "content": "hi"}])
    assert r is not None
    assert calls[-1] == "deepseek-v4-flash-0731"   # 第 4 次用 fallback
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_llm_client.py::test_chat_fallback_on_retry_exhausted -v`
Expected: FAIL(当前 3 次 429 后返回 None)

- [ ] **Step 3: 实现**

`llm_client.py` 的 `chat` 方法,在 for 循环结束后(3 次都失败)加 fallback 逻辑:

```python
        # V1.11 容灾:主模型重试耗尽 → 切 fallback 模型重试 1 次(RETRY_STATUS 类错误)
        fallback = settings.fallback_model
        if (fallback and fallback != self.model):
            logger.warning("主模型 %s 重试耗尽,切 fallback %s", self.model, fallback)
            fb_payload = {**payload, "model": fallback}
            try:
                resp = httpx.post(f"{self.base_url}/chat/completions",
                                  headers=self._headers(), json=fb_payload,
                                  timeout=self.timeout)
                if resp.status_code not in self.NO_RETRY_STATUS:
                    resp.raise_for_status()
                    return self._parse(resp)
            except httpx.HTTPError as exc:
                logger.warning("fallback 模型调用失败: %s", exc)
        return None
```

注意:需要在循环外、`return None` 前插入;`settings` 已在文件顶部 import(`from app.config import settings` 确认)。`_parse` 返回的 ChatResult.model 来自响应 data.model,天然是 fallback 模型名。

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_llm_client.py -v`
Expected: PASS(含既有用例 + 新 fallback 用例)

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/agent/llm_client.py tests/test_llm_client.py
git commit -m "feat(fallback): LLMClient 容灾 — 主模型重试耗尽切 fallback 模型重试"
```

---

### Task 5:fallback 审计标记(degraded + 实际模型)

**Files:**
- Modify: `ai-service/app/agent/llm.py`
- Test: `ai-service/tests/test_llm.py`(追加)

**Interfaces:**
- Consumes: Task 4 的 `ChatResult.model`(实际用模型)。
- Produces: `OpenAICompatibleLLM` 各方法在成功路径的 `_audit_model_call` 中,`model=` 用实际模型;若实际模型 ≠ 路由模型,`degraded=True`。Task 3 成本聚合因此能区分主/备模型成本。

- [ ] **Step 1: 写失败测试**

```python
def test_audit_records_fallback_model(monkeypatch):
    from app.agent import model_router
    from app.config import settings
    monkeypatch.setattr(settings, "hypothesize_model", "qwen3.8-max")
    captured = {}
    class _Client:
        def chat_json_with_usage(self, messages, max_tokens=600, model=None):
            return {"hypotheses": [{"description": "h"}]}, {"input_tokens": 10, "output_tokens": 5}, "stop"
    def fake_audit(state, node, *, model, degraded, **kw):
        captured["model"] = model
        captured["degraded"] = degraded
    from app.agent.llm import OpenAICompatibleLLM
    llm = OpenAICompatibleLLM(client=_Client(), strict=True)
    monkeypatch.setattr(llm, "_audit_model_call", fake_audit)
    llm.hypothesize({"description": "库存慢", "incident_id": 1, "run_id": 1})
    assert captured["model"] == "qwen3.8-max"
    assert captured["degraded"] is False
```

(注:若希望同时断言 fallback 场景,可在 Task 5 的测试中 monkeypatch `llm._chat_json_with_usage` 返回 fallback 模型名,断言 audit 的 model 是 fallback 且 degraded=True。)

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && .venv/Scripts/pytest.exe <testfile>::test_audit_records_fallback_model -v`
Expected: FAIL(当前审计 `model=settings.chat_model_resolved` 或 degraded 固定 False)

- [ ] **Step 3: 实现**

`llm.py` 各节点方法的成功路径 `_audit_model_call(..., model=..., degraded=...)`:
- `model=` 从 `_chat_json_with_usage` 返回值中取实际模型:把 `self._chat_json_with_usage(...)` 改为能感知模型——最简单:节点方法在调用后取 `data, usage, finish = self._chat_json_with_usage(...)`,再 `actual_model = self.client.model if ... `。更干净的方式:让 `_chat_json_with_usage` 返回 4 元组(加实际模型)。

建议实现:`_chat_json_with_usage` 改为返回 `(data, usage, finish, actual_model)`,调用处解包并传给 `_audit_model_call(model=actual_model, degraded=(actual_model != routed_model))`。逐节点更新 hypothesize/select_tool/reflect/write_report 的审计调用。

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && .venv/Scripts/pytest.exe <testfile> -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/agent/llm.py <testfile>
git commit -m "feat(fallback): 审计记录实际模型 + degraded 标记(区分主/备模型成本)"
```

---

### Task 6:整体回归 + 修复

**Files:**
- 全部改动文件。

**Interfaces:**
- 无新接口;验证 Task 1-5 集成。

- [ ] **Step 1: 后端全量测试**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/ -q`
Expected: 全部 PASS(原 391 + 新增,无回归)。若失败,逐个修复:重点检查 `_chat_json_with_usage` 返回值变化(4 元组)是否漏改调用处、`LLMClient` fallback 是否破坏既有 mock 测试。

- [ ] **Step 2: 冒烟路由 + 成本**

```bash
cd ai-service && .venv/Scripts/python.exe -c "
from app.agent.model_router import route
from app.agent.cost import aggregate_model_costs
print('route(select_tool):', route('select_tool'))
print('cost:', aggregate_model_costs([{'model':'qwen3.7-flash','input_tokens':100,'output_tokens':50}]))
"
```

Expected: `route(select_tool): None`(默认未配置)+ 成本正确输出。

- [ ] **Step 3: 提交(如有修复)**

```bash
git add -A && git commit -m "fix(router): 回归修复"
```

---

### Task 7:VM 真实模型验收

**Files:**
- 无代码改动;部署验证。

**Interfaces:**
- 依赖 Task 1-6 全部代码。

- [ ] **Step 1: 同步代码到 VM 并重建**

```bash
cd ai-service && tar czf ../.reasonix/tmp/ai_v111.tar.gz app
cd .. && python .reasonix/tools/vm_ssh.py put .reasonix/tmp/ai_v111.tar.gz tracemind/ai_v111.tar.gz
timeout 60 python .reasonix/tools/vm_ssh.py run "cd ~/tracemind/ai-service && rm -rf app && tar xzf ../ai_v111.tar.gz && nohup bash -c 'cd ~/tracemind/ai-service && DOCKER_BUILDKIT=0 docker build -t tracemind-ai-service:latest --target ai-runtime . > /tmp/b-ai-v111.log 2>&1; echo EXIT=\$? >> /tmp/b-ai-v111.log' >/dev/null 2>&1 & echo 重建中"
```

轮询日志直到 `EXIT=0`(V1.9/V1.10 踩坑:后台构建 + 轮询)。

- [ ] **Step 2: 环境自查(必做,V1.10 教训)**

```bash
timeout 60 python .reasonix/tools/vm_ssh.py run "docker ps --format '{{.Names}}' | grep -cE 'prometheus|jaeger'; curl -s 'http://127.0.0.1:6333/collections' | python3 -c 'import json,sys; print([c[\"name\"] for c in json.load(sys.stdin)[\"result\"][\"collections\"]])'"
```

Expected: prometheus+jaeger 在跑(≥2);collections 含 `tracemind_runbook_current`。若缺:先 `docker compose --profile observability-ui up -d` + 重建 runbook 索引(V1.10 记忆)。

- [ ] **Step 3: 配置路由 + 切真实模型 + 启动**

```bash
# VM .env.vm 或 compose environment 加路由配置(本地已配置则随代码同步)
timeout 60 python .reasonix/tools/vm_ssh.py run "cd ~/tracemind && \
  grep -q TRACEMIND_HYPOTHESIZE_MODEL .env.vm || echo '
TRACEMIND_HYPOTHESIZE_MODEL=qwen3.8-max
TRACEMIND_SELECT_TOOL_MODEL=qwen3.7-flash
TRACEMIND_REFLECT_MODEL=qwen3.8-max
TRACEMIND_REPORT_MODEL=qwen3.7-flash
TRACEMIND_FALLBACK_MODEL=deepseek-v4-flash-0731' >> .env.vm; \
  sed -i 's/TRACEMIND_LLM_MODE: fake/TRACEMIND_LLM_MODE: real_strict/' compose.yml && \
  sed -i 's/TRACEMIND_RAG_MODE: off/TRACEMIND_RAG_MODE: required/' compose.yml && \
  docker compose up -d --no-build ai-service 2>&1 | tail -1"
```

- [ ] **Step 4: 跑 SCN 验收(真实模型,耗百炼额度)**

```bash
timeout 280 python scripts/verify-m14.py --base http://<vm-host>:8000 --order http://<vm-host>:8081 --rounds 1 --scenario SCN-001
```

Expected: 至少一轮 recovered。**若遇 429/额度错误:立即停止并告知用户(见 tracemind-real-model-quota)。**

- [ ] **Step 5: 验证路由生效 + 成本账单**

通过 VM 查 model_call 表按 node 聚合模型:

```bash
docker exec tracemind-ai python -c "
from sqlalchemy import create_engine, text; import os
e = create_engine(os.environ['TRACEMIND_CONTROL_DB_URL'])
with e.connect() as c:
    rows = c.execute(text(\"SELECT node, model, COUNT(*) FROM model_call GROUP BY node, model\")).fetchall()
    for r in rows: print(r)
"
```

Expected: hypothesize/reflect → qwen3.8-max;select_tool/report → qwen3.7-flash(路由生效)。

本地跑成本聚合(从 VM 导出的 model_call 数据):

```bash
cd ai-service && .venv/Scripts/python.exe -c "
from app.agent.cost import aggregate_model_costs
calls = [...]  # 从 model_call 表导出
print(aggregate_model_costs(calls))
"
```

- [ ] **Step 6: 恢复 VM 默认(fake/off)+ 清理 + 推送**

```bash
timeout 60 python .reasonix/tools/vm_ssh.py run "cd ~/tracemind && sed -i 's/TRACEMIND_LLM_MODE: real_strict/TRACEMIND_LLM_MODE: fake/; s/TRACEMIND_RAG_MODE: required/TRACEMIND_RAG_MODE: off/' compose.yml && docker compose up -d --no-build ai-service 2>&1 | tail -1"
rm -f .reasonix/tmp/ai_v111.tar.gz
git push origin main
```
