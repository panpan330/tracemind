# TraceMind M3 计划:LangGraph 编排(诊断 → 审批 → 修复 → 恢复)

日期:2026-08-10
基于:docs/superpowers/specs/2026-08-10-tracemind-v1-design.md(设计定稿)
前置:M1(Java 目标系统 + MySQL 场景)、M2(AI 服务 + 七工具 + 控制库 + API)全部完成

## 目标

将 M2 的"七工具 + 控制库"编排为 LangGraph 工作流,在**不调用真实 LLM 也能完整跑通闭环**的前提下:
`注入故障 → 创建 Incident → 调查(证据循环)→ E1~E5 根因闸门 → 修复提案 → 人工审批(interrupt)→ 执行修复 → 恢复验证 → 复盘报告`。

验收(verify-m3.py):DEMO_MODE 全自动场景下,一次注入后系统自动完成调查并停在 `awaiting_approval`,人工批准后自动执行修复并确认 `recovered`,输出 postmortem。期间 SSE 事件完整可补发。

## 与设计文档的对应(必须遵循)

- 状态机:`created / investigating / awaiting_approval / executing / verifying / recovered / needs_human / rejected / failed`(设计 4.6)。
- 根因判定:**E1~E5 规则闸门**(设计 4.3),不是 LLM 自由发挥;`collect_evidence` 只调五个只读调查工具。
- `execute_fix` / `verify_recovery` 仅由确定性节点调用,绝不绑定 LLM(设计 5.1)。
- 审批用 LangGraph `interrupt()` + `Command(resume=...)`;审批人身份服务端固定 `DEMO_APPROVER_ID=demo-approver`(设计 4.7)。
- 过期审批:后台每 30 秒扫描 pending 且过期的 Approval → expired → 恢复图 → `report(rejected_or_expired)`(设计 4.7)。
- 恢复判定:修复后**不读含修复前慢请求的滑动窗口**,主动三批固定探测,每批独立 P95;`consecutive_healthy_checks >= 3` 才算 recovered(设计 4.4)。
- 后台执行:单 Uvicorn Worker + asyncio.Task;`thread_id` = `agent_run.thread_id`;启动时从 checkpoint 恢复(设计 8.1)。
- LLM:`LLM_MODE=fake | openai_compatible`;fake 为默认且确定性,用于测试与无密钥环境(设计 4.8)。
- SSE:`incident_event` 持久化 + Last-Event-ID 补发 + 15~30 秒 heartbeat(设计 9)。

## 新增依赖(ai-service)

- `langgraph>=0.2`、`langchain-core>=0.3`(LangGraph 与结构化输出)
- dev:`pytest-asyncio`(async 图测试)

安装:`cd ai-service && uv pip install -e ".[dev]" --index-url https://pypi.tuna.tsinghua.edu.cn/simple -v`

---

## Task 3.1: LangGraph 依赖 + 状态/图骨架

**Files:**
- Modify: `ai-service/pyproject.toml`(加 langgraph / langchain-core)
- Create: `ai-service/app/agent/state.py`(IncidentState TypedDict + reducer)
- Create: `ai-service/app/agent/graph.py`(StateGraph 骨架:占位节点,编译)
- Create: `ai-service/app/agent/nodes.py`(节点函数空壳,后续任务填充)
- Test: `ai-service/tests/test_agent_graph.py`

**Interfaces:**
- Consumes: `app.db.models.AgentRun.thread_id`(M2)。
- Produces: 可编译的 `app.agent.graph.build_graph()`;`IncidentState` 结构。

- [ ] **Step 1: 写失败测试**

`test_agent_graph.py`:

```python
from app.agent.graph import build_graph


def test_graph_compiles():
    graph = build_graph()
    assert graph is not None
```

预期:收集失败(模块不存在)。

- [ ] **Step 2: 确认测试失败**

Run: `cd ai-service && uv run pytest tests/test_agent_graph.py -v`
Expected: ERROR(ModuleNotFoundError)。

- [ ] **Step 3: 定义 IncidentState**

`app/agent/state.py`:

```python
from typing import Annotated, Any, TypedDict


def dedup_by_id(existing: list[dict], updates: list[dict]) -> list[dict]:
    seen = {item["id"] for item in existing}
    merged = list(existing)
    for item in updates:
        if item["id"] not in seen:
            merged.append(item)
            seen.add(item["id"])
    return merged


class IncidentState(TypedDict, total=False):
    incident_id: int
    run_id: int
    thread_id: str
    severity: str
    service_ref: str
    status: str
    investigation_round: int
    max_investigation_rounds: int
    tool_call_count: int
    max_tool_calls: int
    termination_reason: str | None
    hypotheses: Annotated[list[dict], dedup_by_id]      # id 去重
    evidence: Annotated[list[dict], dedup_by_id]        # id 去重
    confirmed_hypothesis_id: int | None
    fix_proposal: dict | None
    approval: dict | None
    fix_execution: dict | None
    recovery: dict | None
    report: dict | None
    error: str | None
```

- [ ] **Step 4: 图骨架(占位节点 + 编译)**

`app/agent/nodes.py` 先放空实现(`return state` 透传),`graph.py`:

```python
from langgraph.graph import END, START, StateGraph

from app.agent.state import IncidentState


def build_graph():
    g = StateGraph(IncidentState)
    g.add_node("noop", lambda s: s)
    g.add_edge(START, "noop")
    g.add_edge("noop", END)
    return g.compile()
```

- [ ] **Step 5: 确认测试通过**

Run: `cd ai-service && uv run pytest tests/test_agent_graph.py -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add ai-service/pyproject.toml ai-service/app/agent/ ai-service/tests/test_agent_graph.py ai-service/uv.lock
git commit -m "feat(agent): LangGraph 状态与图骨架"
```

---

## Task 3.2: 确定性节点(证据循环 / E1~E5 闸门 / 恢复规则)

**Files:**
- Create: `ai-service/app/agent/rules.py`(E1~E5 判定 + 恢复规则)
- Modify: `ai-service/app/agent/nodes.py`(collect_evidence / diagnose / verify_recovery_node)
- Modify: `ai-service/app/agent/graph.py`(真实节点 + 条件边)
- Modify: `ai-service/tests/test_agent_graph.py`(图流程测试)

**Interfaces:**
- Consumes: 七工具(经 `app.tools.execute.execute_tool`,incident_id 传审计;结果存 evidence)。
- Produces: `IncidentState.confirmed_hypothesis_id` / `recovery`。

- [ ] **Step 1: 写失败测试(规则 + 证据循环)**

`tests/test_rules.py`:

```python
from app.agent.rules import evaluate_evidence_gate


def test_gate_requires_all_e1_to_e5():
    evidence = {"E1": True, "E2": True, "E3": True, "E4": True, "E5": False}
    assert evaluate_evidence_gate(evidence) is False


def test_gate_passes_when_all_met():
    evidence = {f"E{i}": True for i in range(1, 6)}
    assert evaluate_evidence_gate(evidence) is True
```

- [ ] **Step 2: 确认失败**

Run: `cd ai-service && uv run pytest tests/test_rules.py -v`
Expected: ERROR(ModuleNotFoundError)。

- [ ] **Step 3: 实现规则**

`app/agent/rules.py`:

```python
GATE_EVIDENCE = {"E1", "E2", "E3", "E4", "E5"}


def evaluate_evidence_gate(evidence: dict[str, bool]) -> bool:
    """E1~E5 全部满足才确认根因(设计 4.3)。"""
    return all(evidence.get(k) is True for k in sorted(GATE_EVIDENCE))


def evaluate_recovery_rule(probes: list[dict], baseline_p95: float | None,
                           threshold_ratio: float = 1.2) -> bool:
    """设计 4.4:三批固定探测,每批独立 P95;全部 <= 基线 x 1.2 才算恢复。"""
    if len(probes) < 3:
        return False
    if baseline_p95 is None:
        return False
    return all(p["p95_ms"] is not None and p["p95_ms"] <= baseline_p95 * threshold_ratio
               for p in probes)
```

- [ ] **Step 4: 实现节点**

`nodes.py` 增加:

```python
def collect_evidence(state: IncidentState) -> dict:
    """调查预算内依次调用五个只读调查工具,产出 E1~E5 判定与 evidence。"""
    # 每次调用 execute_tool(incident_id=state["incident_id"]) 并写 evidence/tool_call_count
    # E1: get_service_metrics(service_ref, window_seconds=300) -> p95 异常?
    # E2: get_trace(representative_slow_trace_id) -> database 阶段耗时占比高?
    # E3: list_expensive_query_digests(incident_id) -> rows_examined_delta 大?
    # E4: get_query_plan(INVENTORY_LOOKUP, {skuId, warehouseId}) -> access_type == ALL?
    # E5: get_index_info(inventory) -> idx_sku_warehouse 缺失?
    # 每项成功即追加 evidence;预算耗尽(round >= max 或 tool_call_count >= max)设置
    # termination_reason 并进入 needs_human。

def diagnose(state: IncidentState) -> dict:
    # 从 evidence 汇总 E1~E5 布尔;evaluate_evidence_gate 通过则写 confirmed_hypothesis_id
    # 并 status=investigating;未通过且预算还有 -> 回到 collect_evidence(条件边)

def verify_recovery_node(state: IncidentState) -> dict:
    # 调用 verify_recovery 工具(incident_id, fix_execution_id)
    # 按 evaluate_recovery_rule 更新 state["recovery"] 与 status
```

- [ ] **Step 5: 图接线(条件边)**

`graph.py`:

```python
g.add_node("collect_evidence", collect_evidence)
g.add_node("diagnose", diagnose)
g.add_node("verify_recovery", verify_recovery_node)
# collect_evidence -> diagnose
# diagnose -> collect_evidence(证据不足且预算未耗尽,条件)
# diagnose -> END(证据不足且预算耗尽 -> needs_human,状态机由状态字段表达)
```

- [ ] **Step 6: 图流程测试(使用 monkeypatch 的假工具)**

`test_agent_graph.py` 增加:构造一个 E1~E5 全满足的假工具响应,断言图从 collect_evidence 走到 diagnose 且 confirmed_hypothesis_id 被设置。

- [ ] **Step 7: 运行全部测试**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全部通过。

- [ ] **Step 8: 提交**

```bash
git add ai-service/app/agent/ ai-service/tests/
git commit -m "feat(agent): 确定性节点(证据循环/E1-E5 闸门/恢复规则)"
```

---

## Task 3.3: LLM 节点(hypothesize / propose_fix / report)+ FakeLLM

**Files:**
- Create: `ai-service/app/agent/llm.py`(FakeLLM + openai_compatible 封装)
- Modify: `ai-service/app/agent/nodes.py`(hypothesize / propose_fix / report)
- Modify: `ai-service/app/agent/graph.py`(接入新节点)
- Modify: `ai-service/tests/test_agent_graph.py`(全流程 fake 模式测试)

**Interfaces:**
- Consumes: `app.config.settings.llm_mode`(fake | openai_compatible)。
- Produces: `hypotheses`(结构化)、`fix_proposal`(含 parameters_hash)、`report`。

- [ ] **Step 1: 写失败测试**

`tests/test_llm.py`:断言 FakeLLM.hypothesize 返回确定性列表(含"缺少联合索引"假设);`report` 输入假事实输出含根因。

- [ ] **Step 2: 确认失败**

Run: `cd ai-service && uv run pytest tests/test_llm.py -v`
Expected: ERROR。

- [ ] **Step 3: 实现 FakeLLM**

`app/agent/llm.py`:

```python
class FakeLLM:
    """确定性占位实现:不调网络,返回场景内建假设/提案/报告。"""

    def hypothesize(self, state) -> list[dict]:
        return [{"id": "h1", "description": "缺少联合索引 idx_sku_warehouse(sku_id, warehouse_id) 导致慢查询",
                 "status": "proposed"}]

    def propose_fix(self, state) -> dict:
        # 返回 {action_type: "CREATE_INVENTORY_INDEX", risk_level, parameters, reason}
        # parameters_hash 由 sha256(parameters json) 计算

    def write_report(self, state) -> dict:
        # 只使用 state 中已落库事实(evidence/fix/recovery)拼装 markdown
```

`openai_compatible` 分支:占位(标注 V1.1 接真实模型),本任务只保证 FakeLLM 可用且 `llm_mode` 可配置。

- [ ] **Step 4: 实现节点**

- `hypothesize`:调用 llm.hypothesize → 写 hypotheses 列表 + evidence(假设产生)→ status=investigating。
- `propose_fix`:confirmed 后调用 llm.propose_fix → fix_proposal(写入 fix_proposal 表,M3 需要 repository 支持)→ status=awaiting_approval。
- `report`:调用 llm.write_report → postmortem 表 → status 终态(recovered/needs_human/...)。

补充 repository:`app/repositories/proposal_repo.py`(create_proposal / get_proposal)、`app/repositories/postmortem_repo.py`(create_postmortem)。

- [ ] **Step 5: 图接线 + 全流程 fake 测试**

`graph.py` 全节点接线:

```
START -> ingest -> hypothesize -> collect_evidence <-> diagnose(条件)
diagnose(confirmed) -> propose_fix -> human_approval(interrupt)
human_approval(approved, resume) -> execute_fix -> verify_recovery -> report -> END
human_approval(rejected) -> report(rejected_or_expired) -> END
```

fake 模式全流程测试:mock 工具返回 E1~E5 满足 → 断言走到 awaiting_approval。

- [ ] **Step 6: 运行全部测试**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全部通过。

- [ ] **Step 7: 提交**

```bash
git add ai-service/app/agent/ ai-service/app/repositories/ ai-service/tests/
git commit -m "feat(agent): LLM 节点 + FakeLLM(确定性,可配置 openai_compatible)"
```

---

## Task 3.4: 审批中断(interrupt / resume / 过期扫描)

**Files:**
- Modify: `ai-service/app/agent/graph.py`(human_approval 节点 + interrupt)
- Modify: `ai-service/app/agent/nodes.py`(approval 相关节点)
- Create: `ai-service/app/api/approvals.py`(审批决策端点)
- Create: `ai-service/app/services/approval_scanner.py`(过期扫描)
- Modify: `ai-service/app/api/runs.py`(investigations 改为 async task 启动图)
- Modify: `ai-service/app/main.py`(挂 approvals 路由 + 启动扫描)
- Modify: `ai-service/tests/test_approvals.py`

**Interfaces:**
- Consumes: `langgraph.interrupt()` / `Command(resume=...)`。
- Produces: `POST /api/incidents/{id}/approvals/{approval_id}/decision`。

- [ ] **Step 1: 写失败测试**

`tests/test_approvals.py`:POST decision approved → 图从 interrupt 恢复,状态进入 executing(用直接调图 + fake 工具模拟)。

- [ ] **Step 2: 确认失败**

Run: `cd ai-service && uv run pytest tests/test_approvals.py -v`
Expected: ERROR。

- [ ] **Step 3: 实现审批节点**

`nodes.py`:

```python
def human_approval(state: IncidentState) -> dict:
    # 写 approval 表(pending,expires_at = now + 10min)
    # langgraph.interrupt({"type": "approval_request", "proposal": state["fix_proposal"], "approval_id": id})
    # resume 后:decision == approved -> status=executing, 返回 approval 记录
    #          decision == rejected -> status=rejected, 返回 rejected
```

- [ ] **Step 4: 审批 API + 恢复图**

`api/approvals.py`:

```python
@router.post("/{incident_id}/approvals/{approval_id}/decision")
async def decision(incident_id: int, approval_id: int, body: DecisionIn):
    # 校验 approval 属于 incident、pending、未过期、proposal 未变
    # approved_by 服务端固定 DEMO_APPROVER_ID=demo-approver
    # 更新 approval 状态 -> 用 thread_id 调 graph.ainvoke(Command(resume={"decision": ...}))
    # 返回最新 Incident 状态
```

- [ ] **Step 5: 过期扫描**

`services/approval_scanner.py`:每 30 秒扫描 `status='pending' AND expires_at < now` 的 Approval → 置 expired → 恢复图(resume decision=rejected,comment="expired")。在 `main.py` 用 FastAPI lifespan 启动;`DEMO_MODE` 或测试环境可关闭。

- [ ] **Step 6: 运行全部测试**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全部通过。

- [ ] **Step 7: 提交**

```bash
git add ai-service/app/agent/ ai-service/app/api/approvals.py ai-service/app/services/approval_scanner.py ai-service/app/main.py ai-service/tests/
git commit -m "feat(agent): 审批中断与恢复 + 过期自动扫描"
```

---

## Task 3.5: SSE 事件流

**Files:**
- Create: `ai-service/app/api/stream.py`(SSE 端点)
- Modify: `ai-service/app/main.py`(挂路由)
- Modify: `ai-service/tests/test_stream.py`

**Interfaces:**
- Consumes: `app.repositories.event_repo.list_events`(M2)。
- Produces: `GET /api/incidents/{id}/stream`。

- [ ] **Step 1: 写失败测试**

`tests/test_stream.py`:创建 Incident → 调 SSE 端点 → 断言首包快照(incident 状态)+ 已有事件,且 heartbeat 注释行出现。

- [ ] **Step 2: 确认失败**

Run: `cd ai-service && uv run pytest tests/test_stream.py -v`
Expected: ERROR。

- [ ] **Step 3: 实现 SSE**

`stream.py`:

```python
@router.get("/{incident_id}/stream")
async def stream(incident_id: int, request: Request):
    # Last-Event-ID 解析 -> after_sequence
    # 快照先行:incident 当前状态 + 该 incident 全部事件(sequence > after)
    # 之后每 2 秒轮询 event_repo 新增事件;15~30 秒一次 heartbeat(: ping)
    # 事件格式:event: <event_type>\ndata: <json>\nid: <sequence>\n
    # 客户端断开即退出
```

- [ ] **Step 4: 运行全部测试**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全部通过。

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/api/stream.py ai-service/app/main.py ai-service/tests/test_stream.py
git commit -m "feat(api): SSE 事件流(快照+Last-Event-ID 补发+heartbeat)"
```

---

## Task 3.6: 启动恢复 + 调查接口异步化

**Files:**
- Create: `ai-service/app/services/runner.py`(asyncio.Task 管理 + 启动恢复)
- Modify: `ai-service/app/api/runs.py`(investigations 202 异步)
- Modify: `ai-service/app/main.py`(lifespan 启动恢复)
- Modify: `ai-service/tests/test_runner.py`

**Interfaces:**
- Consumes: `agent_run.status`(investigating/executing/verifying 为未完成)。
- Produces: `POST /api/incidents/{id}/investigations` 202 + 后台 task。

- [ ] **Step 1: 写失败测试**

`tests/test_runner.py`:创建 Incident + run → 调 `runner.start_investigation` → 断言返回 thread_id 且 agent_run.status 变为 investigating。

- [ ] **Step 2: 确认失败**

Run: `cd ai-service && uv run pytest tests/test_runner.py -v`
Expected: ERROR。

- [ ] **Step 3: 实现 runner**

`services/runner.py`:

```python
_tasks: dict[int, asyncio.Task] = {}


async def start_investigation(incident_id: int, run_id: int, thread_id: str) -> None:
    # 更新 agent_run.status=investigating
    # 创建 task: graph.ainvoke(initial_state, thread_id=thread_id, config={"recursion_limit": 100})
    # 完成后更新 agent_run.status 终态与 finished_at

def resume_investigation(incident_id: int, thread_id: str, resume_value: dict) -> None:
    # 创建 task: graph.ainvoke(Command(resume=resume_value), thread_id=thread_id)

def recover_pending_runs() -> None:
    # 启动时扫描 status in (investigating, executing, verifying) 的 agent_run
    # 对每个:用 thread_id 继续图(interrupt 处等待 resume;无中断则继续)
```

- [ ] **Step 4: investigations 端点改造**

`runs.py`:

```python
@router.post("/{incident_id}/investigations", status_code=202)
async def start_investigation(incident_id: int):
    # 创建 run(thread_id 固定为 agent_run.thread_id)
    # await runner.start_investigation(incident_id, run.id, run.thread_id)
    return {"run_id": run.id, "thread_id": run.thread_id, "status": "investigating"}
```

- [ ] **Step 5: lifespan 恢复**

`main.py`:`@asynccontextmanager` lifespan 中调用 `recover_pending_runs()`(先于服务接收流量)。

- [ ] **Step 6: 运行全部测试**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全部通过。

- [ ] **Step 7: 提交**

```bash
git add ai-service/app/services/runner.py ai-service/app/api/runs.py ai-service/app/main.py ai-service/tests/
git commit -m "feat(agent): 后台执行 + 启动 checkpoint 恢复"
```

---

## Task 3.7: M3 验收(端到端闭环)

**Files:**
- Create: `scripts/verify-m3.py`

**Interfaces:**
- Consumes: AI 服务全部 API。

- [ ] **Step 1: 写验收脚本**

`scripts/verify-m3.py` 流程:

1. `reset` + `inject`(DEMO_MODE 代理)。
2. `POST /api/incidents`(title/severity/service_ref)→ 201;`POST /{id}/investigations` → 202。
3. 轮询 `GET /api/incidents/{id}` 直到 `status == awaiting_approval`(超时 60s)。
4. 断言:hypotheses 含目标假设;evidence 覆盖 E1~E5;approval 存在且 pending。
5. `POST /api/incidents/{id}/approvals/{approval_id}/decision {"decision": "approved"}` → 轮询直到 `recovered`(超时 60s)。
6. 断言:fix_execution 状态 succeeded(或 no_op);recovery_check status=recovered;postmortem 非空。
7. `reset` 清理。打印 PASS/FAIL。

- [ ] **Step 2: 执行验收**

前置:inventory-service(DEMO_MODE)、order-service、AI 服务已启动。

```bash
cd scripts && python verify-m3.py
```

Expected: PASS 且输出各阶段时间线。

- [ ] **Step 3: 回归全部测试**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全部通过。

- [ ] **Step 4: 提交**

```bash
git add scripts/verify-m3.py
git commit -m "feat(scripts): M3 端到端验收脚本"
```

---

## M3 完成标准

- [ ] `test_agent_graph.py` / `test_rules.py` / `test_llm.py` / `test_approvals.py` / `test_stream.py` / `test_runner.py` 全绿。
- [ ] `verify-m3.py` PASS:注入 → 自动调查 → awaiting_approval → 批准 → 执行 → recovered → postmortem。
- [ ] SSE 端点在调查期间可见事件流且支持 Last-Event-ID 补发。
- [ ] 过期审批扫描:手动把 approval 过期时间改到过去,30 秒内状态变 expired 且 Incident 变 needs_human(或 rejected)。
- [ ] 不依赖真实 LLM(LLM_MODE=fake 默认)。
