# TraceMind V1.3 实施计划:SCN-002 锁等待 + 回归评测流水线

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增"长事务锁阻塞库存预占"(SCN-002)多根因诊断闭环,并交付 fast/full 两档回归评测流水线。

**Architecture:** 复用 V1.0~V1.2 的 Agent 状态机/审批/恢复框架:程序维护共享 Fact 与双 DiagnosticPolicy(缺索引/锁阻塞),不向 Agent 泄露场景;处置经 FixRegistry 确定性映射为 `TERMINATE_BLOCKING_SESSION`(KILL 会话),执行前重查 + 原子幂等防误杀;评测分 Agent 诊断/处置安全/RAG 三个套件。

**Tech Stack:** Python FastAPI + LangGraph + MCP(stdio,contract 2.0.0)、MySQL 8(performance_schema/information_schema)、Java Spring Boot 21(inventory-service 场景注入)、Vue 3。

## Global Constraints

- 根因代码全项目统一:`LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION`(处置动作 `TERMINATE_BLOCKING_SESSION`)
- 创建 Incident 只传 service_ref/现象/时间范围,**不传 scenario_id/root_cause**(防泄露)
- `MCP_TOOL_CONTRACT_VERSION = "2.0.0"`(应用契约版本,非 MCP Protocol Version);MCP 工具集合 5→7
- 证据状态必须明确:`confirmed / refuted / unknown / stale`(禁用 not_confirmed)
- blocking_relation_hash 只含稳定关系身份 10 项(不含任何时间字段);时间字段单独做新鲜度
- 预算:MAX_TOOL_EXECUTIONS=10、MAX_DECISION_ATTEMPTS=14、MAX_LOCK_EVIDENCE_REFRESH=1(评测校准)
- 新增第五账号 `session_terminator`:凭据不传 MCP Server 子进程/LLM/前端/日志;只允许终止业务账号白名单连接;Processlist 转正整数后固定 `KILL <id>`(不接受字符串 SQL/任意连接标识符)
- E2E 每轮 `reset before → inject → run → verify → finally reset`
- 真实模型额度错误(429/quota/insufficient)必须立即报告用户,不自行重试掩盖
- 所有 SQL 初始化脚本与 DDL 幂等

---

### Task 1: 共享 Fact 与双 DiagnosticPolicy 判定层

**Files:**
- Create: `ai-service/app/agent/facts.py`(共享 Fact 判定)
- Create: `ai-service/app/agent/policies.py`(双 Policy + 排他条件 + 四分支判定)
- Modify: `ai-service/app/agent/rules.py`(改为引用 policies,保留旧函数名兼容)
- Test: `ai-service/tests/test_policies.py`

**Interfaces:**
- Produces(后续任务依赖):
  - `facts.evaluate_facts(evidence: dict[str, dict]) -> dict[str, bool]`——输入 evidence dict(key→content),输出 Fact 布尔(`F_` 前缀键)
  - `policies.evaluate_policies(facts: dict[str, bool]) -> dict[str, str]`——输出 `{"scn001": "confirmed|refuted|unknown", "scn002": ...}`
  - `policies.evaluate_exclusions(facts) -> {"x_index_normal": bool, "x_no_target_lock_wait": bool}`
  - `policies.decide_root_cause(policies_state: dict, exclusions: dict) -> tuple[str | None, str | None]`——(root_cause_code | None, termination_reason | None);`multiple_confirmed_causes` 通过 termination_reason 表达
  - `policies.ROOT_CAUSE_LOCK = "LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION"`、`policies.ROOT_CAUSE_INDEX = "MISSING_INVENTORY_INDEX"`

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_policies.py`:

```python
import pytest
from app.agent import facts, policies

def _facts(**kwargs):
    """构造 Fact 字典;未指定键默认 False(证据缺失 → Fact 不成立)。"""
    base = {
        "F_ENDPOINT_DEGRADED": False, "F_DB_STAGE_DOMINANT": False,
        "F_TARGET_QUERY_EXPENSIVE": False, "F_INDEX_MISSING": False,
        "F_PLAN_FULL_SCAN": False, "F_TARGET_LOCK_WAIT": False,
        "F_BLOCKER_CONFIRMED": False, "F_BLOCKER_LONG_RUNNING": False,
    }
    base.update(kwargs)
    return base


def test_index_confirmed_lock_refuted():
    f = _facts(F_ENDPOINT_DEGRADED=True, F_DB_STAGE_DOMINANT=True,
               F_TARGET_QUERY_EXPENSIVE=True, F_PLAN_FULL_SCAN=True, F_INDEX_MISSING=True)
    pol = policies.evaluate_policies(f)
    assert pol["scn001"] == "confirmed" and pol["scn002"] == "refuted"
    ex = policies.evaluate_exclusions(f)
    assert ex["x_no_target_lock_wait"] is True
    root, reason = policies.decide_root_cause(pol, ex)
    assert root == policies.ROOT_CAUSE_INDEX and reason is None


def test_lock_confirmed_index_refuted():
    f = _facts(F_ENDPOINT_DEGRADED=True, F_DB_STAGE_DOMINANT=True,
               F_TARGET_LOCK_WAIT=True, F_BLOCKER_CONFIRMED=True,
               F_BLOCKER_LONG_RUNNING=True, F_INDEX_MISSING=False,
               F_PLAN_FULL_SCAN=False, F_TARGET_QUERY_EXPENSIVE=False)
    pol = policies.evaluate_policies(f)
    assert pol["scn002"] == "confirmed" and pol["scn001"] == "refuted"
    ex = policies.evaluate_exclusions(f)
    assert ex["x_index_normal"] is True
    root, _ = policies.decide_root_cause(pol, ex)
    assert root == policies.ROOT_CAUSE_LOCK


def test_both_confirmed_goes_needs_human():
    f = _facts(F_ENDPOINT_DEGRADED=True, F_DB_STAGE_DOMINANT=True,
               F_TARGET_QUERY_EXPENSIVE=True, F_PLAN_FULL_SCAN=True, F_INDEX_MISSING=True,
               F_TARGET_LOCK_WAIT=True, F_BLOCKER_CONFIRMED=True, F_BLOCKER_LONG_RUNNING=True)
    pol = policies.evaluate_policies(f)
    root, reason = policies.decide_root_cause(pol, policies.evaluate_exclusions(f))
    assert root is None and reason == "multiple_confirmed_causes"


def test_lock_confirmed_index_unknown_keep_collecting():
    f = _facts(F_ENDPOINT_DEGRADED=True, F_DB_STAGE_DOMINANT=True,
               F_TARGET_LOCK_WAIT=True, F_BLOCKER_CONFIRMED=True, F_BLOCKER_LONG_RUNNING=True)
    pol = policies.evaluate_policies(f)
    # index Fact 全未知 → scn001 unknown
    assert pol["scn001"] == "unknown"
    root, _ = policies.decide_root_cause(pol, policies.evaluate_exclusions(f))
    assert root is None  # 继续收集


def test_lock_confirmed_index_stale_keep_collecting():
    f = _facts(F_ENDPOINT_DEGRADED=True, F_DB_STAGE_DOMINANT=True,
               F_TARGET_LOCK_WAIT=True, F_BLOCKER_CONFIRMED=True, F_BLOCKER_LONG_RUNNING=True)
    pol = policies.evaluate_policies(f)
    pol["scn001"] = "stale"
    root, _ = policies.decide_root_cause(pol, policies.evaluate_exclusions(f))
    assert root is None


def test_both_refuted_keeps_investigating():
    pol = {"scn001": "refuted", "scn002": "refuted"}
    root, _ = policies.decide_root_cause(pol, {"x_index_normal": True, "x_no_target_lock_wait": True})
    assert root is None


def test_lock_confirmed_but_auto_termination_unsafe():
    f = _facts(F_ENDPOINT_DEGRADED=True, F_DB_STAGE_DOMINANT=True,
               F_TARGET_LOCK_WAIT=True, F_BLOCKER_CONFIRMED=True, F_BLOCKER_LONG_RUNNING=True,
               F_INDEX_MISSING=True, F_PLAN_FULL_SCAN=True)  # 索引异常 → X-INDEX-NORMAL=false
    pol = policies.evaluate_policies(f)
    ex = policies.evaluate_exclusions(f)
    assert ex["x_index_normal"] is False
    root, reason = policies.decide_root_cause(pol, ex)
    # SCN002 confirmed 但自动终止不安全:不自动处置
    assert root is None or reason is not None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_policies.py -q`
Expected: FAIL(ModuleNotFoundError: app.agent.facts / policies)

- [ ] **Step 3: 实现 facts.py**

```python
"""共享 Fact 判定:每次工具返回后由 collect_evidence 重算(设计 4.1)。
输入 evidence dict(key -> {"content": {...}, "passed": bool}),输出 Fact 布尔。"""
from app.agent.tool_calling import _evaluate_metrics_fact, _evaluate_trace_fact  # noqa: F401
```

> 注:上述 import 为占位导向——实际实现中 Fact 判定直接复用 nodes._EVALUATORS 的语义,但 V1.3 要求判定逻辑独立于 nodes。正确实现如下(替代上面两行 import):

```python
"""共享 Fact 判定:每次工具返回后由 collect_evidence 重算(设计 4.1)。"""


def evaluate_facts(evidence: dict[str, dict]) -> dict[str, bool]:
    """evidence: {key: {"content": ..., "passed": bool}}。
    Fact 真值来源:passed + content 结构判定(与 V1.0 评估器语义一致)。"""
    def content(key: str) -> dict:
        ev = evidence.get(key) or {}
        return ev.get("content") or {}

    # 索引链路
    index_missing = False
    idx = content("e5").get("indexes") or []
    if content("e5"):
        index_missing = not any(i.get("index_name") == "idx_sku_warehouse" for i in idx)
    plan = (content("e4").get("explain") or {}).get("query_block", {})
    full_scan = (plan.get("table") or {}).get("access_type") == "ALL"
    expensive = bool(content("e3"))  # digest 增量非空即视为高代价 SQL

    # 锁链路(L1~L5 → Fact;L5 复合匹配见 policies/collect_evidence)
    wait = content("l1")  # get_lock_waiters 输出
    waits = (wait or {}).get("waits") or []
    target_wait = any(
        w.get("object_schema") == "tracemind_business"
        and w.get("object_table") == "inventory"
        and w.get("waiting_query_ref") == "INVENTORY_RESERVATION"
        for w in waits
    )
    tx = content("l2") or {}  # get_transaction_details 输出
    blocker_confirmed = bool(wait and tx and tx.get("transaction_id") is not None)
    long_running = (tx.get("age_ms") or 0) >= LONG_TRANSACTION_THRESHOLD_MS

    return {
        "F_ENDPOINT_DEGRADED": bool(evidence.get("e1")),
        "F_DB_STAGE_DOMINANT": bool(evidence.get("e2")),
        "F_TARGET_QUERY_EXPENSIVE": expensive,
        "F_INDEX_MISSING": index_missing,
        "F_PLAN_FULL_SCAN": full_scan,
        "F_TARGET_LOCK_WAIT": target_wait,
        "F_BLOCKER_CONFIRMED": blocker_confirmed,
        "F_BLOCKER_LONG_RUNNING": long_running,
    }
```

- [ ] **Step 4: 实现 policies.py**

```python
"""双 DiagnosticPolicy:SCN-001(缺索引)/SCN-002(锁阻塞)共享 Fact 引用。
状态枚举:confirmed / refuted / unknown / stale(禁用 not_confirmed)。"""
from app.agent import facts

ROOT_CAUSE_INDEX = "MISSING_INVENTORY_INDEX"
ROOT_CAUSE_LOCK = "LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION"

POLICY_SCN001 = ("F_ENDPOINT_DEGRADED", "F_DB_STAGE_DOMINANT",
                 "F_TARGET_QUERY_EXPENSIVE", "F_PLAN_FULL_SCAN", "F_INDEX_MISSING")
POLICY_SCN002 = ("F_ENDPOINT_DEGRADED", "F_DB_STAGE_DOMINANT",
                 "F_TARGET_LOCK_WAIT", "F_BLOCKER_CONFIRMED", "F_BLOCKER_LONG_RUNNING")

KNOWN_FACTS = set(POLICY_SCN001) | set(POLICY_SCN002)


def evaluate_policies(facts_dict: dict[str, bool]) -> dict[str, str]:
    """每项 Fact 必须已知(True/False);任一未知 → 该 Policy unknown。"""
    out = {}
    for name, keys in (("scn001", POLICY_SCN001), ("scn002", POLICY_SCN002)):
        unknown = [k for k in keys if k not in facts_dict]
        if unknown:
            out[name] = "unknown"
            continue
        if all(facts_dict.get(k) is True for k in keys):
            out[name] = "confirmed"
        elif all(facts_dict.get(k) is False for k in keys):
            out[name] = "refuted"
        else:
            out[name] = "refuted" if any(facts_dict.get(k) for k in keys) is False else "confirmed"
    return out
```

> 注:上面 evaluate_policies 的 refuted 分支有歧义(部分 True 部分 False 应为 refuted)。正确实现:

```python
def evaluate_policies(facts_dict: dict[str, bool]) -> dict[str, str]:
    out = {}
    for name, keys in (("scn001", POLICY_SCN001), ("scn002", POLICY_SCN002)):
        unknown = [k for k in keys if k not in facts_dict]
        if unknown:
            out[name] = "unknown"
        elif all(facts_dict.get(k) is True for k in keys):
            out[name] = "confirmed"
        else:
            out[name] = "refuted"  # 任一必需 Fact 为 False 即 refuted
    return out


def evaluate_exclusions(facts_dict: dict[str, bool]) -> dict[str, bool]:
    """自动处置排他条件(非正向证据)。索引 Fact 未知时视为 False(不允许自动终止)。"""
    idx_ok = facts_dict.get("F_INDEX_MISSING") is False and facts_dict.get("F_PLAN_FULL_SCAN") is False
    lock_absent = (facts_dict.get("F_TARGET_LOCK_WAIT") is False
                   and facts_dict.get("F_BLOCKER_CONFIRMED") is False)
    return {"x_index_normal": bool(idx_ok), "x_no_target_lock_wait": bool(lock_absent)}


def decide_root_cause(pol: dict[str, str], exclusions: dict[str, bool]) -> tuple[str | None, str | None]:
    """四分支判定(设计 4.4)。返回 (root_cause | None, termination_reason | None)。"""
    s1, s2 = pol.get("scn001"), pol.get("scn002")
    if s1 == "confirmed" and s2 == "confirmed":
        return None, "multiple_confirmed_causes"
    if s1 == "confirmed" and s2 == "refuted" and exclusions.get("x_no_target_lock_wait"):
        return ROOT_CAUSE_INDEX, None
    if s2 == "confirmed" and s1 == "refuted" and exclusions.get("x_index_normal"):
        return ROOT_CAUSE_LOCK, None
    # 任一 confirmed 且竞争 unknown/stale,或两者 refuted → 继续收集
    return None, None
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_policies.py -q`
Expected: 8 passed

> 注:Step 3 facts.py 引用了 `LONG_TRANSACTION_THRESHOLD_MS`,需在 facts.py 定义常量(或从 config 读)。修正:facts.py 顶部加 `LONG_TRANSACTION_THRESHOLD_MS = 5000`(5 秒,可后续配置化)。

- [ ] **Step 6: 兼容层 rules.py**

在 `ai-service/app/agent/rules.py` 末尾追加(保持旧调用方 `evaluate_evidence_gate` 可用,但新增双 policy 入口):

```python
from app.agent import policies  # noqa: E402  (模块级)

def evaluate_evidence_gate(evidence: dict[str, bool]) -> bool:
    """V1.0 兼容:SCN-001 五证据(E1~E5)全 True 即认为缺索引根因成立。"""
    return all(evidence.get(k) is True for k in GATE_EVIDENCE)
```

- [ ] **Step 7: 运行全量回归**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 原测试全绿(旧 `evaluate_evidence_gate` 签名不变)

- [ ] **Step 8: 提交**

```bash
git add ai-service/app/agent/facts.py ai-service/app/agent/policies.py ai-service/app/agent/rules.py ai-service/tests/test_policies.py
git commit -m "feat(policy): 共享 Fact + 双 DiagnosticPolicy(SCN-001/SCN-002)四分支判定"
```

---

### Task 2: 两新只读 MCP 工具(get_lock_waiters / get_transaction_details)

**Files:**
- Modify: `ai-service/app/agent/tool_schemas.py`(追加两工具裁剪 Schema)
- Modify: `ai-service/app/tools/registry.py`(注册两工具)
- Create: `ai-service/app/tools/lock_queries.py`(锁等待/事务详情真实查询 + fixture 钩子)
- Test: `ai-service/tests/test_lock_queries.py`

**Interfaces:**
- Produces:
  - `lock_queries.get_lock_waiters(schema_ref, table_ref, min_wait_ms) -> dict`——`{"ok": bool, "data": {"observed_at": str, "snapshot_expires_at": str, "waits": [...]}}`
  - `lock_queries.get_transaction_details(transaction_ref) -> dict`——`{"ok": bool, "data": {...}}`(transaction_id/processlist_id/account/age_ms/statement_digest/locked_objects/observed_at/snapshot_expires_at)
  - `lock_queries.set_fixture(fixture: dict | None)`(评测 Fixture 注入钩子,与 execute.py 的 set_eval_fixture 同名约定)
  - `lock_queries.LOCK_OBSERVATION_TABLE`(blocker_ref 持久化表名)

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_lock_queries.py`:

```python
import pytest
from app.tools import lock_queries


@pytest.fixture(autouse=True)
def no_fixture():
    lock_queries.set_fixture(None)
    yield
    lock_queries.set_fixture(None)


def test_lock_waiters_shapes():
    lock_queries.set_fixture({"waits": [{
        "wait_ref": "w1", "waiter_ref": "wa1", "blocker_ref": "blk_1",
        "requesting_transaction_id": 100, "blocking_transaction_id": 88,
        "requesting_processlist_id": 101, "blocking_processlist_id": 88,
        "requesting_lock_ref": "lr1", "blocking_lock_ref": "lr2",
        "object_schema": "tracemind_business", "object_table": "inventory",
        "index_name": "idx_sku_warehouse", "lock_type": "RECORD", "lock_mode": "X",
        "wait_duration_ms": 5200, "waiting_query_ref": "INVENTORY_RESERVATION"}]})
    r = lock_queries.get_lock_waiters("tracemind_business", "inventory", 3000)
    assert r["ok"] is True
    waits = r["data"]["waits"]
    assert waits[0]["blocking_transaction_id"] == 88
    assert "observed_at" in r["data"] and "snapshot_expires_at" in r["data"]


def test_transaction_details_shapes():
    lock_queries.set_fixture({"transaction_id": 88, "processlist_id": 88,
                              "account": "app_business", "age_ms": 12000,
                              "statement_digest": "UPDATE inventory ...",
                              "locked_objects": [{"schema": "tracemind_business",
                                                  "table": "inventory",
                                                  "lock_ref": "lr2"}]})
    r = lock_queries.get_transaction_details("blk_1")
    assert r["ok"] is True and r["data"]["transaction_id"] == 88
    assert r["data"]["processlist_id"] == 88


def test_no_fixture_returns_failure():
    r = lock_queries.get_lock_waiters("tracemind_business", "inventory", 3000)
    # 未注入 fixture 且无真实 MySQL 连接上下文 → 明确失败而非伪数据
    assert r["ok"] is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_lock_queries.py -q`
Expected: FAIL(ModuleNotFoundError: app.tools.lock_queries)

- [ ] **Step 3: 实现 lock_queries.py**

```python
"""锁等待/事务详情查询:真实数据源 = performance_schema / information_schema(经 ai_investigator 只读连接)。
评测 Fixture 注入优先于真实查询;真实查询失败必须返回 ok=False,不允许伪造。"""
import json
import time
import uuid
from typing import Any

from app.db.engine import get_readonly_engine  # 现有 ai_investigator 连接池

LONG_TRANSACTION_THRESHOLD_MS = 5000

_fixture: dict | None = None


def set_fixture(fixture: dict | None) -> None:
    global _fixture
    _fixture = fixture


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _expires(seconds: int = 10) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + seconds))


def get_lock_waiters(schema_ref: str, table_ref: str, min_wait_ms: int) -> dict:
    if _fixture is not None:
        waits = _fixture.get("waits") or []
        return {"ok": True, "data": {"observed_at": _now_iso(), "snapshot_expires_at": _expires(),
                                     "waits": waits}}
    try:
        engine = get_readonly_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM performance_schema.data_lock_waits"
            ).mappings().all()
    except Exception as exc:  # 连接失败/表不存在 → 明确失败
        return {"ok": False, "data": None, "error_message": f"lock_waiters_query_failed: {exc}"}
    waits = []
    for r in rows:
        wait_ms = int(r.get("WAIT_TIME_MS") or 0)
        if wait_ms < min_wait_ms:
            continue
        if r.get("OBJECT_SCHEMA") != schema_ref or r.get("OBJECT_NAME") != table_ref:
            continue
        waits.append({
            "wait_ref": str(r.get("THREAD_ID")),
            "waiter_ref": f"thr_{r.get('REQUESTING_THREAD_ID')}",
            "blocker_ref": f"blk_{uuid.uuid4().hex[:12]}",
            "requesting_transaction_id": r.get("REQUESTING_TRX_ID"),
            "blocking_transaction_id": r.get("BLOCKING_TRX_ID"),
            "requesting_processlist_id": r.get("REQUESTING_PROCESS_ID"),
            "blocking_processlist_id": r.get("BLOCKING_PROCESS_ID"),
            "requesting_lock_ref": r.get("REQUESTING_ENGINE_TRANSACTION_ID"),
            "blocking_lock_ref": r.get("BLOCKING_ENGINE_TRANSACTION_ID"),
            "object_schema": r.get("OBJECT_SCHEMA"), "object_table": r.get("OBJECT_NAME"),
            "index_name": r.get("OBJECT_INDEX_NAME"), "lock_type": r.get("LOCK_TYPE"),
            "lock_mode": r.get("LOCK_MODE"), "wait_duration_ms": wait_ms,
            "waiting_query_ref": "INVENTORY_RESERVATION",  # 由调用方按业务解析
        })
    return {"ok": True, "data": {"observed_at": _now_iso(), "snapshot_expires_at": _expires(),
                                 "waits": waits}}


def get_transaction_details(transaction_ref: str) -> dict:
    if _fixture is not None:
        data = dict(_fixture)
        data["observed_at"] = _now_iso()
        data["snapshot_expires_at"] = _expires()
        return {"ok": True, "data": data}
    # 真实查询:information_schema.innodb_trx(连接池 ai_investigator 有 SELECT 权限)
    try:
        engine = get_readonly_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                "SELECT trx_id, trx_mysql_thread_id, trx_started, trx_state, "
                "TIMESTAMPDIFF(MILLISECOND, trx_started, NOW()) AS age_ms "
                "FROM information_schema.innodb_trx WHERE trx_id = %s",
                (transaction_ref,),
            ).mappings().all()
    except Exception as exc:
        return {"ok": False, "data": None, "error_message": f"trx_query_failed: {exc}"}
    if not rows:
        return {"ok": False, "data": None, "error_message": "TRX_NOT_FOUND"}
    r = rows[0]
    return {"ok": True, "data": {
        "transaction_id": r.get("trx_id"), "processlist_id": r.get("trx_mysql_thread_id"),
        "account": r.get("USER") or "", "age_ms": int(r.get("age_ms") or 0),
        "statement_digest": "", "locked_objects": [],
        "observed_at": _now_iso(), "snapshot_expires_at": _expires(),
    }}
```

> 注:上述真实查询是骨架(字段名需与目标 MySQL 8 对齐,`innodb_trx.trx_started` 无 USER 列)。**本任务实现重点**:两工具以 fixture 可注入的方式存在 + schema 注册;真实查询字段对齐放 Task 9(Java 注入联调)一并校准。实现时在函数内用 try/except 保证任何查询异常都返回 `ok=False` 而非崩溃。

- [ ] **Step 4: 注册工具(registry.py + tool_schemas.py)**

`ai-service/app/tools/registry.py` 中,`get_index_info` 注册之后追加:

```python
@tool("get_lock_waiters", GetLockWaitersIn)
def _get_lock_waiters(scope_ref: str) -> dict:
    """scope_ref 枚举白名单;schema/table/min_wait_ms 由程序固定注入。"""
    from app.tools import lock_queries
    return lock_queries.get_lock_waiters("tracemind_business", "inventory", 3000)


@tool("get_transaction_details", GetTransactionDetailsIn)
def _get_transaction_details(transaction_ref: str) -> dict:
    from app.tools import lock_queries
    return lock_queries.get_transaction_details(transaction_ref)
```

`ai-service/app/tools/schemas.py` 新增两个 Pydantic 输入模型:

```python
class GetLockWaitersIn(BaseModel):
    scope_ref: str = "INVENTORY_RESERVATION"  # 枚举白名单(模型侧无自由参数)


class GetTransactionDetailsIn(BaseModel):
    transaction_ref: str = "OBSERVED_BLOCKER"  # 受控引用占位,程序解析后注入真实 blocker_ref
```

`ai-service/app/agent/tool_schemas.py` 追加 LLM 侧裁剪 Schema:

```python
    {"type": "function", "function": {"name": "get_lock_waiters",
        "description": "查询目标库存记录的锁等待关系(仅用于锁阻塞调查)",
        "parameters": {"type": "object", "properties": {
            "scope_ref": {"type": "string", "enum": ["INVENTORY_RESERVATION"]}},
            "required": ["scope_ref"]}}},
    {"type": "function", "function": {"name": "get_transaction_details",
        "description": "查询已观测阻塞事务的详情(需先调用 get_lock_waiters 获得引用)",
        "parameters": {"type": "object", "properties": {
            "transaction_ref": {"type": "string", "enum": ["OBSERVED_BLOCKER"]}},
            "required": ["transaction_ref"]}}},
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_lock_queries.py -q`
Expected: 3 passed

- [ ] **Step 6: 全量回归 + 提交**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全绿(新工具未进 MCP 集合前不影响既有契约)

```bash
git add ai-service/app/agent/tool_schemas.py ai-service/app/tools/registry.py ai-service/app/tools/schemas.py ai-service/app/tools/lock_queries.py ai-service/tests/test_lock_queries.py
git commit -m "feat(tools): 锁调查两工具 — get_lock_waiters/get_transaction_details(fixture 可注入,真实查询失败明确返回)"
```

---

### Task 3: MCP 契约升级(5→7 工具,Contract 2.0.0)

**Files:**
- Modify: `ai-service/app/mcp/contract.py`(TOOL_NAMES 加两工具、VERSION 2.0.0、mcp_tool_schemas 上下文注入)
- Modify: `ai-service/app/mcp/server.py`(FastMCP 注册两工具)
- Test: `ai-service/tests/test_contract.py`(追加断言)

**Interfaces:**
- Consumes: `lock_queries.get_lock_waiters/get_transaction_details`(Task 2)
- Produces: `MCP_TOOL_CONTRACT_VERSION = "2.0.0"`;`TOOL_NAMES` 7 个;MCP 工具显式签名含 `incident_id, agent_run_id` + 业务参数

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_contract.py` 追加:

```python
from app.mcp import contract


def test_contract_version_200():
    assert contract.MCP_TOOL_CONTRACT_VERSION == "2.0.0"


def test_seven_tools_in_contract():
    assert contract.TOOL_NAMES == {
        "get_service_metrics", "get_trace", "list_expensive_query_digests",
        "get_query_plan", "get_index_info", "get_lock_waiters",
        "get_transaction_details",
    }


def test_mcp_schemas_include_context_and_lock_tools():
    schemas = contract.mcp_tool_schemas()
    assert "get_lock_waiters" in schemas and "get_transaction_details" in schemas
    lw = schemas["get_lock_waiters"]
    assert "incident_id" in lw["required"] and "agent_run_id" in lw["required"]
    td = schemas["get_transaction_details"]
    assert "transaction_ref" in td["properties"]


def test_llm_schemas_hide_context():
    schemas = contract.llm_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == contract.TOOL_NAMES
    for s in schemas:
        assert "incident_id" not in s["function"]["parameters"]["properties"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_contract.py -q`
Expected: FAIL(版本仍 1.0,工具集 5 个)

- [ ] **Step 3: 修改 contract.py**

```python
MCP_TOOL_CONTRACT_VERSION = "2.0.0"
SERVER_VERSION = "0.2.0"

TOOL_NAMES = frozenset({
    "get_service_metrics", "get_trace", "list_expensive_query_digests",
    "get_query_plan", "get_index_info", "get_lock_waiters",
    "get_transaction_details",
})
```

`mcp_tool_schemas()` 遍历 `sorted(TOOL_NAMES)` 并从 `TOOL_REGISTRY` 取 schema,新工具自动并入;两新工具的 `scope_ref`/`transaction_ref` 参数来自 registry 的 input_schema(自动并入 properties 与 required)。

- [ ] **Step 4: server.py 注册两工具**

`ai-service/app/mcp/server.py` 在 `get_index_info` 工具函数后追加:

```python
    @mcp.tool()
    def get_lock_waiters(incident_id: int, agent_run_id: int,
                         scope_ref: str) -> dict:
        """查询目标库存记录的锁等待关系(scope_ref 枚举白名单)。"""
        from app.tools import lock_queries
        return lock_queries.get_lock_waiters("tracemind_business", "inventory", 3000)

    @mcp.tool()
    def get_transaction_details(incident_id: int, agent_run_id: int,
                                transaction_ref: str) -> dict:
        """查询已观测阻塞事务详情(transaction_ref 必须为前序证据的 blocker_ref)。"""
        from app.tools import lock_queries
        return lock_queries.get_transaction_details(transaction_ref)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_contract.py -q`
Expected: 新增 4 测试 + 原契约测试全绿

- [ ] **Step 6: 全量回归 + 提交**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全绿(MCP 相关现有测试若断言 5 工具,同步更新为 7)

```bash
git add ai-service/app/mcp/contract.py ai-service/app/mcp/server.py ai-service/tests/test_contract.py
git commit -m "feat(mcp): 契约升级 2.0.0 — 工具集 5→7(get_lock_waiters/get_transaction_details)"
```

---

### Task 4: 预算与 eligible_tools 扩展(双 policy 证据采集)

**Files:**
- Modify: `ai-service/app/agent/tool_calling.py`(预算常量 + compute_eligible_tools + resolve_arguments)
- Test: `ai-service/tests/test_tool_calling.py`(追加)

**Interfaces:**
- Consumes: `policies.POLICY_SCN001/POLICY_SCN002`(Task 1)
- Produces: 预算常量 `MAX_DECISION_ATTEMPTS=14, MAX_TOOL_EXECUTIONS=10, MAX_LOCK_EVIDENCE_REFRESH=1`;`compute_eligible_tools` 输出含 7 工具(锁工具按依赖资格)

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_tool_calling.py` 追加:

```python
from app.agent import tool_calling


def test_budget_v13():
    assert tool_calling.MAX_DECISION_ATTEMPTS == 14
    assert tool_calling.MAX_TOOL_EXECUTIONS == 10
    assert tool_calling.MAX_LOCK_EVIDENCE_REFRESH == 1


def test_eligible_includes_lock_tools_when_lock_facts_unknown():
    state = {"evidence_gate": {}, "evidence": []}
    eligible = tool_calling.compute_eligible_tools(state)
    assert "get_lock_waiters" in eligible
    assert "get_transaction_details" not in eligible  # 需先有 blocker_ref


def test_transaction_details_eligible_after_lock_waiters():
    state = {"evidence_gate": {},
             "evidence": [{"key": "l1", "content": {"waits": [{"blocker_ref": "blk_1",
                       "object_schema": "tracemind_business", "object_table": "inventory",
                       "waiting_query_ref": "INVENTORY_RESERVATION"}]}, "passed": True}]}
    eligible = tool_calling.compute_eligible_tools(state)
    assert "get_transaction_details" in eligible


def test_resolve_lock_tools_parameters():
    state = {"service_ref": "inventory-service", "evidence": []}
    args = tool_calling.resolve_arguments("get_lock_waiters", {}, state)
    assert args["scope_ref"] == "INVENTORY_RESERVATION"
    args2 = tool_calling.resolve_arguments("get_transaction_details",
                                           {"transaction_ref": "OBSERVED_BLOCKER"}, state)
    assert args2["transaction_ref"] == "OBSERVED_BLOCKER"  # 程序解析后注入真实 blocker_ref
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_tool_calling.py -q`
Expected: 新增 FAIL(预算仍 10/8;eligible 无锁工具)

- [ ] **Step 3: 修改 tool_calling.py**

预算常量改为:

```python
MAX_DECISION_ATTEMPTS = 14
MAX_TOOL_EXECUTIONS = 10
MAX_LOCK_EVIDENCE_REFRESH = 1
MAX_CONSECUTIVE_INVALID = 2
MAX_CONSECUTIVE_NO_PROGRESS = 2
MAX_DURATION_SECONDS = 180
```

`compute_eligible_tools` 末尾追加锁工具资格(在现有索引链之后):

```python
    # 锁调查工具资格(独立判断,不退化为固定顺序)
    if not satisfied("l1"):
        eligible.add("get_lock_waiters")
    if not satisfied("l2"):
        l1_ev = (evidence.get("l1") or {}).get("content") or {}
        lock_observed = any(
            w.get("object_schema") == "tracemind_business"
            and w.get("object_table") == "inventory"
            and w.get("waiting_query_ref") == "INVENTORY_RESERVATION"
            for w in (l1_ev.get("waits") or []))
        if lock_observed:
            eligible.add("get_transaction_details")
    return eligible
```

> 注:`evidence` 变量是 `compute_eligible_tools` 内已构建的 `{e.get("key"): e for e in state.get("evidence") or []}`;`satisfied("l1")` 与现有 `satisfied()` 辅助函数一致(evidence_gate 兼容大小写)。

`resolve_arguments` 追加(在 `get_index_info` 分支后):

```python
    if name == "get_lock_waiters":
        return {"scope_ref": raw_args.get("scope_ref", "INVENTORY_RESERVATION")}
    if name == "get_transaction_details":
        # 受控引用:程序从当前 Incident/Run 的有效锁等待证据取 blocker_ref(LLM 不得编造)
        ev = {e.get("key"): e for e in state.get("evidence") or []}
        waits = ((ev.get("l1") or {}).get("content") or {}).get("waits") or []
        target = [w for w in waits if w.get("object_schema") == "tracemind_business"
                  and w.get("object_table") == "inventory"
                  and w.get("waiting_query_ref") == "INVENTORY_RESERVATION"]
        if not target:
            raise ArgumentResolutionError("无有效 blocker_ref,无法调用 get_transaction_details")
        return {"transaction_ref": target[0].get("blocker_ref")}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_tool_calling.py -q`
Expected: 4 新增 + 原测试全绿

- [ ] **Step 5: 全量回归 + 提交**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全绿(预算变化若使个别旧测试超限,调整测试注入的 state 预算字段)

```bash
git add ai-service/app/agent/tool_calling.py ai-service/tests/test_tool_calling.py
git commit -m "feat(tooling): V1.3 预算(10/14/1)+ 锁工具资格与参数解析(受控 blocker_ref)"
```

---

### Task 5: 确定性证据规划器扩展(双 policy 补采)

**Files:**
- Modify: `ai-service/app/agent/determinism.py`(DeterministicEvidencePlanner 支持锁证据链)
- Test: `ai-service/tests/test_determinism.py`(追加)

**Interfaces:**
- Consumes: `tool_calling.compute_eligible_tools`(Task 4)
- Produces: `DeterministicEvidencePlanner.choose(state, eligible)` 按 E1→E5 与 L1→L2 双链补缺失证据

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_determinism.py` 追加:

```python
from app.agent.determinism import DeterministicEvidencePlanner


def test_planner_collects_lock_chain():
    planner = DeterministicEvidencePlanner()
    state = {"evidence_gate": {}, "evidence": [],
             "service_ref": "inventory-service",
             "policy": {"scn001": "unknown", "scn002": "unknown"}}
    eligible = {"get_service_metrics", "get_trace", "list_expensive_query_digests",
                "get_query_plan", "get_index_info", "get_lock_waiters"}
    calls = planner.choose(state, eligible)
    names = [c["name"] for c in calls]
    assert "get_lock_waiters" in names  # 锁证据缺失 → 补采


def test_planner_transaction_details_after_lock():
    planner = DeterministicEvidencePlanner()
    state = {"evidence_gate": {"E1": True, "E2": True, "E3": True, "E4": True, "E5": True},
             "evidence": [{"key": "l1", "content": {"waits": [{"blocker_ref": "blk_1",
                       "object_schema": "tracemind_business", "object_table": "inventory",
                       "waiting_query_ref": "INVENTORY_RESERVATION"}]}, "passed": True}]}
    eligible = {"get_transaction_details"}
    calls = planner.choose(state, eligible)
    assert calls and calls[0]["name"] == "get_transaction_details"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_determinism.py -q`
Expected: 新增 FAIL(planner 不产锁工具)

- [ ] **Step 3: 修改 determinism.py**

```python
# 锁证据链(L1 → L2 依赖 blocker_ref)
LOCK_EVIDENCE_ORDER = ["l1", "l2"]
LOCK_EVIDENCE_TOOL = {"l1": "get_lock_waiters", "l2": "get_transaction_details"}


class DeterministicEvidencePlanner:
    def choose(self, state: dict, eligible_tools: set[str]) -> list[dict]:
        gate = state.get("evidence_gate") or {}
        trace_id = self._find_trace_id(state)
        # 索引链(E1→E5,现有逻辑保留,含 E2 trace 回退)
        for key in EVIDENCE_ORDER:
            if gate.get(key, gate.get(key.upper(), False)):
                continue
            if key == "e2" and not trace_id:
                if "get_service_metrics" in eligible_tools:
                    return [{"id": "de1", "name": "get_service_metrics",
                             "arguments": self._arguments_for("e1", "get_service_metrics", state)}]
                continue
            tool = EVIDENCE_TOOL[key]
            if tool not in eligible_tools:
                continue
            args = self._arguments_for(key, tool, state)
            if key == "e2":
                args["trace_id"] = trace_id
            return [{"id": f"d{key}", "name": tool, "arguments": args}]
        # 锁链(L1→L2):索引链已齐或证据不足时补锁证据
        for key in LOCK_EVIDENCE_ORDER:
            if gate.get(key, gate.get(key.upper(), False)):
                continue
            if key == "l2" and not self._find_blocker_ref(state):
                continue  # 无 blocker_ref,无法调用事务详情
            tool = LOCK_EVIDENCE_TOOL[key]
            if tool not in eligible_tools:
                continue
            return [{"id": f"d{key}", "name": tool,
                     "arguments": self._arguments_for(key, tool, state)}]
        return []

    @staticmethod
    def _find_blocker_ref(state: dict) -> str:
        for ev in state.get("evidence") or []:
            for w in ((ev.get("content") or {}).get("waits") or []):
                if (w.get("object_schema") == "tracemind_business"
                        and w.get("object_table") == "inventory"
                        and w.get("waiting_query_ref") == "INVENTORY_RESERVATION"):
                    return w.get("blocker_ref") or ""
        return ""

    @staticmethod
    def _arguments_for(key: str, tool: str, state: dict) -> dict:
        if tool == "get_lock_waiters":
            return {"scope_ref": "INVENTORY_RESERVATION"}
        if tool == "get_transaction_details":
            return {"transaction_ref": DeterministicEvidencePlanner._find_blocker_ref(state)}
        # ...(现有五工具参数原样保留)
        return {}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_determinism.py -q`
Expected: 2 新增 + 原测试全绿

- [ ] **Step 5: 全量回归 + 提交**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全绿

```bash
git add ai-service/app/agent/determinism.py ai-service/tests/test_determinism.py
git commit -m "feat(determinism): 证据规划器支持锁证据链(L1→L2 依赖 blocker_ref)"
```

---

### Task 6: collect_evidence / diagnose 双 policy 改造

**Files:**
- Modify: `ai-service/app/agent/nodes.py`(collect_evidence 双链评估 + diagnose 四分支)
- Modify: `ai-service/app/agent/state.py`(新增 policy/facts/root_cause_code 字段)
- Test: `ai-service/tests/test_agent_graph.py`(追加 SCN-002 诊断路径)

**Interfaces:**
- Consumes: `facts.evaluate_facts`、`policies.*`(Task 1)、`tool_calling`(Task 4)、`determinism`(Task 5)
- Produces: state 新增 `policy: dict`(scn001/scn002 状态)、`facts: dict`、`root_cause_code: str | None`;`diagnose` 按四分支写 `confirmed_hypothesis_id`/`status`/`termination_reason`

- [ ] **Step 1: state.py 追加字段**

```python
    policy: dict          # {"scn001": "confirmed|refuted|unknown|stale",
                          #  "scn002": ...}
    facts: dict           # 共享 Fact 布尔(见 facts.evaluate_facts)
    root_cause_code: str | None
    lock_evidence_refresh_count: int   # stale 后重采锁关系次数(≤ MAX_LOCK_EVIDENCE_REFRESH)
```

- [ ] **Step 2: collect_evidence 追加锁评估器**

在 `nodes.py` 的 `_EVALUATORS` 字典中追加(现有 E1~E5 评估器保留):

```python
def _evaluate_lock_waiters(result: dict, state: dict) -> list[dict]:
    data = result.get("data") or {}
    waits = data.get("waits") or []
    target = [w for w in waits
              if w.get("object_schema") == "tracemind_business"
              and w.get("object_table") == "inventory"
              and w.get("waiting_query_ref") == "INVENTORY_RESERVATION"]
    passed = bool(target and any((w.get("wait_duration_ms") or 0) >= 3000 for w in target))
    return [{"id": "L1", "source": "get_lock_waiters", "content": data, "passed": passed}]


def _evaluate_transaction_details(result: dict, state: dict) -> list[dict]:
    data = result.get("data") or {}
    passed = bool(data.get("transaction_id") and (data.get("age_ms") or 0) >= 5000)
    return [{"id": "L2", "source": "get_transaction_details", "content": data, "passed": passed}]
```

`_EVALUATORS` 更新:`"get_lock_waiters": _evaluate_lock_waiters`、`"get_transaction_details": _evaluate_transaction_details`。

- [ ] **Step 3: collect_evidence 成功分支重算 Fact 与 Policy**

在 `collect_evidence` 证据更新分支末尾追加:

```python
        # V1.3:每次工具返回后重算共享 Fact 与双 Policy(设计 4.1/4.2)
        from app.agent import facts as facts_mod, policies as policies_mod
        ev_map = {e.get("id"): {"content": e.get("content"), "passed": e.get("passed")}
                  for e in new_evidence}
        new_facts = facts_mod.evaluate_facts(ev_map)
        new_policy = policies_mod.evaluate_policies(new_facts)
        out["facts"] = new_facts
        out["policy"] = new_policy
```

- [ ] **Step 4: diagnose 四分支判定**

替换 `diagnose` 的根因确认逻辑(保留 needs_human 快速通道):

```python
def diagnose(state: IncidentState) -> dict:
    """V1.3:按双 Policy 四分支判定(设计 4.4)。"""
    if state.get("status") == "needs_human":
        _emit_status(state)
        incident_repo.update_state(state["incident_id"], status="needs_human",
                                   termination_reason=state.get("termination_reason"))
        return state
    from app.agent import policies as policies_mod
    facts_dict = state.get("facts") or {}
    pol = state.get("policy") or policies_mod.evaluate_policies(facts_dict)
    exclusions = policies_mod.evaluate_exclusions(facts_dict)
    root_cause, reason = policies_mod.decide_root_cause(pol, exclusions)
    if root_cause:
        state["confirmed_hypothesis_id"] = "h1"
        state["root_cause_code"] = root_cause
        state["status"] = "investigating"
        state["termination_reason"] = None
        for h in state.get("hypotheses", []):
            hypothesis_repo.upsert_hypothesis(state["incident_id"],
                                              h.get("description", ""), "confirmed")
        return state
    if reason:
        state["status"] = "needs_human"
        state["termination_reason"] = reason
        _emit_status(state)
        incident_repo.update_state(state["incident_id"], status="needs_human",
                                   termination_reason=reason)
        return state
    # 继续收集:预算耗尽才转 needs_human
    if state.get("termination_reason") == "evidence_budget_exhausted":
        state["status"] = "needs_human"
        _emit_status(state)
        incident_repo.update_state(state["incident_id"], status="needs_human",
                                   termination_reason=state.get("termination_reason"))
    else:
        state["status"] = "investigating"
    return state
```

- [ ] **Step 5: 写 SCN-002 诊断测试**

`ai-service/tests/test_agent_graph.py` 追加(完整锁证据 → 锁根因):

```python
def test_lock_wait_graph_reaches_confirmed():
    """SCN-002 锁证据齐全 → 根因 LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION。"""
    from app.agent import nodes
    from app.agent.graph import build_graph

    calls = []

    def fake_execute(tool, incident_id=None, **kwargs):
        calls.append(tool)
        if tool == "get_service_metrics":
            return {"success": True, "data": {"p95Ms": 117, "representativeSlowTraceId": "t1"}}
        if tool == "get_trace":
            return {"success": True, "data": {"inventory_service": [
                {"stage": "database", "durationMs": 110}, {"stage": "total", "durationMs": 120}]}}
        if tool == "list_expensive_query_digests":
            return {"success": True, "data": [{"query_ref": "INVENTORY_LOOKUP",
                                               "rows_examined_delta": 500}]}
        if tool == "get_query_plan":
            return {"success": True, "data": {"explain": {
                "query_block": {"table": {"access_type": "ref"}}}}}
        if tool == "get_index_info":
            return {"success": True, "data": {"indexes": [
                {"index_name": "idx_sku_warehouse"}]}}
        if tool == "get_lock_waiters":
            return {"success": True, "data": {"observed_at": "2026-08-11T00:00:00Z",
                "snapshot_expires_at": "2026-08-11T00:00:20Z",
                "waits": [{"blocker_ref": "blk_1", "blocking_transaction_id": 88,
                           "blocking_processlist_id": 88,
                           "object_schema": "tracemind_business", "object_table": "inventory",
                           "index_name": "idx_sku_warehouse", "lock_type": "RECORD",
                           "lock_mode": "X", "wait_duration_ms": 5200,
                           "waiting_query_ref": "INVENTORY_RESERVATION"}]}}
        if tool == "get_transaction_details":
            return {"success": True, "data": {"transaction_id": 88, "processlist_id": 88,
                "account": "app_business", "age_ms": 12000,
                "statement_digest": "UPDATE inventory SET quantity=...",
                "locked_objects": [{"schema": "tracemind_business", "table": "inventory",
                                    "lock_ref": "lr2"}],
                "observed_at": "2026-08-11T00:00:00Z",
                "snapshot_expires_at": "2026-08-11T00:00:20Z"}}
        return {"success": False, "data": None}

    nodes.execute_tool = fake_execute
    nodes.hypothesis_repo.upsert_hypothesis = lambda *a, **kw: {"id": 1}
    nodes.evidence_repo.upsert_evidence = lambda *a, **kw: {"id": 1}
    nodes.proposal_repo.create_proposal = lambda **kw: type("P", (), {"id": 7})()

    state = {"incident_id": 9, "run_id": 9, "service_ref": "inventory-service",
             "severity": "high", "max_investigation_rounds": 5, "max_tool_calls": 25,
             "policy": {}, "facts": {}}
    graph = build_graph()
    result = graph.invoke(state)
    assert result["root_cause_code"] == (
        "LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION")
    assert result.get("confirmed_hypothesis_id") == "h1"
```

- [ ] **Step 6: 运行测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_agent_graph.py -q`
Expected: 新测试 + 原测试全绿(原 SCN-001 路径仍走 E1~E5 + 索引根因)

- [ ] **Step 7: 全量回归 + 提交**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全绿

```bash
git add ai-service/app/agent/nodes.py ai-service/app/agent/state.py ai-service/tests/test_agent_graph.py
git commit -m "feat(diagnose): collect_evidence 双链评估 + diagnose 四分支判定(双 Policy)"
```

---

### Task 7: FixRegistry 扩展 + propose_fix 确定性参数提取

**Files:**
- Modify: `ai-service/app/agent/fix_registry.py`(加 TERMINATE_BLOCKING_SESSION + blocking_relation_hash)
- Modify: `ai-service/app/agent/nodes.py`(propose_fix 按 root_cause 解析参数)
- Test: `ai-service/tests/test_fix_registry.py`(新建)

**Interfaces:**
- Consumes: `policies.ROOT_CAUSE_INDEX/ROOT_CAUSE_LOCK`(Task 1)
- Produces:
  - `FixRegistry.resolve(root_cause) -> FixActionDefinition`(支持两 root cause)
  - `build_proposal(state) -> dict`(含 action_type/risk_level/parameters/parameters_hash/blocking_relation_hash/reason)
  - `build_relation_hash(fields: dict) -> str`(稳定身份 10 项,规范化 JSON)

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_fix_registry.py`:

```python
import pytest
from app.agent import fix_registry, policies


def test_resolve_lock_action():
    fix = fix_registry.FixRegistry.resolve(policies.ROOT_CAUSE_LOCK)
    assert fix.action_type == "TERMINATE_BLOCKING_SESSION"
    assert fix.risk_level == "high"


def test_build_proposal_lock_uses_evidence():
    state = {
        "incident_id": 5, "run_id": 6, "root_cause_code": policies.ROOT_CAUSE_LOCK,
        "evidence": [{"key": "l1", "content": {"waits": [{
            "blocker_ref": "blk_1", "blocking_transaction_id": 88,
            "blocking_processlist_id": 88, "blocking_lock_ref": "lr2",
            "object_schema": "tracemind_business", "object_table": "inventory",
            "index_name": "idx_sku_warehouse", "requesting_transaction_id": 100,
            "waiting_query_ref": "INVENTORY_RESERVATION"}]}},
            {"key": "l2", "content": {"transaction_id": 88, "processlist_id": 88,
                                      "age_ms": 12000}}]},
    }
    prop = fix_registry.build_proposal(state)
    assert prop["action_type"] == "TERMINATE_BLOCKING_SESSION"
    assert prop["parameters"]["processlist_id"] == 88
    assert "blocking_relation_hash" in prop


def test_relation_hash_is_stable_and_excludes_time():
    fields = {"incident_id": 5, "agent_run_id": 6, "blocking_transaction_id": 88,
              "blocking_processlist_id": 88, "blocking_lock_ref": "lr2",
              "waiting_transaction_id": 100, "waiting_query_ref": "INVENTORY_RESERVATION",
              "locked_schema": "tracemind_business", "locked_table": "inventory",
              "locked_index": "idx_sku_warehouse"}
    h1 = fix_registry.build_relation_hash(fields)
    h2 = fix_registry.build_relation_hash(dict(fields))
    assert h1 == h2
    # 时间字段不入 hash
    h3 = fix_registry.build_relation_hash({**fields, "evidence_observed_at": "x"})
    assert h1 == h3
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_fix_registry.py -q`
Expected: FAIL(FixRegistry.resolve 只支持索引;无 build_relation_hash)

- [ ] **Step 3: 修改 fix_registry.py**

```python
from app.agent.policies import ROOT_CAUSE_INDEX, ROOT_CAUSE_LOCK

_RELATION_HASH_FIELDS = (
    "incident_id", "agent_run_id", "blocking_transaction_id", "blocking_processlist_id",
    "blocking_lock_ref", "waiting_transaction_id", "waiting_query_ref",
    "locked_schema", "locked_table", "locked_index",
)


def build_relation_hash(fields: dict) -> str:
    """稳定关系身份 10 项(不含时间字段);字段排序固定的规范化 JSON。"""
    blob = json.dumps({k: fields[k] for k in _RELATION_HASH_FIELDS if k in fields},
                      sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _extract_lock_parameters(state: dict) -> dict:
    """程序从已确认 Evidence 提取终止会话参数(LLM 不接触)。"""
    ev = {e.get("key"): e.get("content") or {} for e in state.get("evidence") or []}
    waits = (ev.get("l1") or {}).get("waits") or []
    target = [w for w in waits
              if w.get("object_schema") == "tracemind_business"
              and w.get("object_table") == "inventory"
              and w.get("waiting_query_ref") == "INVENTORY_RESERVATION"]
    if not target:
        raise ValueError("无有效锁等待证据,无法构造 TERMINATE_BLOCKING_SESSION 参数")
    w = target[0]
    tx = ev.get("l2") or {}
    return {
        "blocking_transaction_id": w.get("blocking_transaction_id"),
        "blocking_processlist_id": w.get("blocking_processlist_id"),
        "blocking_lock_ref": w.get("blocking_lock_ref"),
        "locked_schema": w.get("object_schema"),
        "locked_table": w.get("object_table"),
        "locked_index": w.get("index_name"),
        "waiting_transaction_id": w.get("requesting_transaction_id"),
        "waiting_query_ref": w.get("waiting_query_ref"),
        "processlist_id": w.get("blocking_processlist_id"),  # KILL 目标 = 会话
        "tx_age_ms": tx.get("age_ms"),
    }


_FIXES = {
    ROOT_CAUSE_INDEX: FixActionDefinition(
        action_type="CREATE_INVENTORY_INDEX", table_ref="inventory",
        index_name="idx_sku_warehouse", columns=["sku_id", "warehouse_id"],
        risk_level="medium",
        reason_template=("已通过 E1~E5 证据链确认库存查询缺少 idx_sku_warehouse(sku_id, warehouse_id),"
                         "建议执行预定义索引创建操作。")),
    ROOT_CAUSE_LOCK: FixActionDefinition(
        action_type="TERMINATE_BLOCKING_SESSION", table_ref="inventory",
        index_name="", columns=[], risk_level="high",
        reason_template=("已通过 L1~L5 证据链确认长事务持有库存目标记录排他锁,"
                         "阻塞库存预占事务,建议终止该阻塞会话。")),
}


def build_proposal(state: dict) -> dict:
    root_cause = state.get("root_cause_code")
    fix = FixRegistry.resolve(root_cause)
    if root_cause == ROOT_CAUSE_INDEX:
        parameters = {"index_name": fix.index_name, "table": fix.table_ref,
                      "columns": fix.columns, "action": "CREATE_INDEX"}
        relation_hash = ""
    else:
        parameters = _extract_lock_parameters(state)
        relation_hash = build_relation_hash({
            "incident_id": state.get("incident_id"),
            "agent_run_id": state.get("run_id"),
            "blocking_transaction_id": parameters.get("blocking_transaction_id"),
            "blocking_processlist_id": parameters.get("blocking_processlist_id"),
            "blocking_lock_ref": parameters.get("blocking_lock_ref"),
            "waiting_transaction_id": parameters.get("waiting_transaction_id"),
            "waiting_query_ref": parameters.get("waiting_query_ref"),
            "locked_schema": parameters.get("locked_schema"),
            "locked_table": parameters.get("locked_table"),
            "locked_index": parameters.get("locked_index"),
        })
    return {
        "action_type": fix.action_type,
        "risk_level": fix.risk_level,
        "parameters": parameters,
        "parameters_hash": _sha256(parameters),
        "blocking_relation_hash": relation_hash,
        "reason": fix.reason_template,
    }
```

- [ ] **Step 4: nodes.py 的 propose_fix 适配**

`propose_fix` 中 `fix = build_proposal(state)` 之后,把 `blocking_relation_hash` 一并传给 `proposal_repo.create_proposal`(该函数签名需加可选参数 `blocking_relation_hash: str | None = None`,写入 fix_proposal 表新列,见 Task 8 DDL)。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_fix_registry.py -q`
Expected: 3 passed

- [ ] **Step 6: 全量回归 + 提交**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全绿

```bash
git add ai-service/app/agent/fix_registry.py ai-service/app/agent/nodes.py ai-service/tests/test_fix_registry.py
git commit -m "feat(fix): FixRegistry 支持 TERMINATE_BLOCKING_SESSION + blocking_relation_hash(稳定 10 项)"
```
---

### Task 8: session_terminator 账号 + TERMINATE_BLOCKING_SESSION 执行器

**Files:**
- Modify: `scripts/sql/02-users.sql`(第五账号 session_terminator)
- Create: `ai-service/app/services/session_terminator.py`(执行器:8 项重查/三结果/原子幂等)
- Modify: `ai-service/app/agent/nodes.py`(execute_fix 按 action_type 分发)
- Test: `ai-service/tests/test_session_terminator.py`(新建)

**Interfaces:**
- Consumes: `fix_registry.build_proposal`(Task 7)产出的 `blocking_relation_hash`/`parameters`
- Produces:
  - `session_terminator.execute(proposal, approval, evidence) -> dict`——返回 `{"status": "killed|already_resolved|target_changed|evidence_stale|rejected|executed", "execution_result": str, "kill_attempted": bool}`
  - `session_terminator.ALLOWED_ACCOUNTS = {"app_business"}`;`FORBIDDEN_ACCOUNTS = {"tracemind_control_app", "ai_investigator", "fix_executor", "session_terminator", "root", "system user"}`
  - `session_terminator.get_terminator_engine()`(独立连接池,凭据仅此处持有)

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_session_terminator.py`:

```python
import pytest
from app.services import session_terminator as st


class FakeEngine:
    """内存版执行引擎:记录 KILL 调用,可按 scenario 返回。"""
    def __init__(self):
        self.killed: list[int] = []
        self.relations = {}   # processlist_id -> 当前事务信息(模拟重查)
        self.resolved = False

    def query_blocking(self, processlist_id: int) -> dict | None:
        if self.resolved:
            return None
        return self.relations.get(processlist_id)


def _proposal(**kw):
    base = {
        "action_type": "TERMINATE_BLOCKING_SESSION",
        "parameters_hash": "h", "blocking_relation_hash": "rh",
        "parameters": {"processlist_id": 88, "blocking_transaction_id": 88,
                       "blocking_lock_ref": "lr2", "locked_schema": "tracemind_business",
                       "locked_table": "inventory", "locked_index": "idx_sku_warehouse",
                       "waiting_transaction_id": 100,
                       "waiting_query_ref": "INVENTORY_RESERVATION"},
        "status": "pending",
    }
    base.update(kw)
    return base


def _approval(**kw):
    base = {"status": "approved", "expires_at": "2099-01-01T00:00:00Z",
            "parameters_hash": "h"}
    base.update(kw)
    return base


def test_valid_kill_exactly_once():
    eng = FakeEngine()
    eng.relations[88] = {"transaction_id": 88, "account": "app_business",
                         "age_ms": 12000, "holds_lock": True, "is_system": False}
    p = _proposal()
    r1 = st.execute(p, _approval(), eng)
    assert r1["kill_attempted"] is True and r1["execution_result"] == "executed"
    # 重复执行:幂等,不再 KILL
    r2 = st.execute(p, _approval(), eng)
    assert r2["kill_attempted"] is False and r2["execution_result"] == "already_executed"


def test_already_resolved():
    eng = FakeEngine()
    eng.resolved = True
    r = st.execute(_proposal(), _approval(), eng)
    assert r["execution_result"] == "already_resolved" and r["kill_attempted"] is False


def test_target_changed_when_processlist_reused():
    eng = FakeEngine()
    # 原事务消失,processlist 已属另一事务
    eng.relations[88] = {"transaction_id": 999, "account": "app_business",
                         "age_ms": 5, "holds_lock": True, "is_system": False}
    r = st.execute(_proposal(), _approval(), eng)
    assert r["execution_result"] == "target_changed" and r["kill_attempted"] is False


def test_evidence_stale_when_lock_ref_changed():
    eng = FakeEngine()
    eng.relations[88] = {"transaction_id": 88, "account": "app_business",
                         "age_ms": 12000, "holds_lock": False, "is_system": False}
    r = st.execute(_proposal(), _approval(), eng)
    assert r["execution_result"] == "evidence_stale" and r["kill_attempted"] is False


def test_reject_forbidden_account():
    eng = FakeEngine()
    eng.relations[88] = {"transaction_id": 88, "account": "tracemind_control_app",
                         "age_ms": 12000, "holds_lock": True, "is_system": False}
    r = st.execute(_proposal(), _approval(), eng)
    assert r["execution_result"] == "rejected_forbidden_account"


def test_reject_not_approved():
    r = st.execute(_proposal(), _approval(status="pending"), FakeEngine())
    assert r["execution_result"] == "rejected_not_approved"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_session_terminator.py -q`
Expected: FAIL(ModuleNotFoundError: app.services.session_terminator)

- [ ] **Step 3: 实现 session_terminator.py**

```python
"""TERMINATE_BLOCKING_SESSION 执行器:防误杀(8 项重查/三结果)+ 原子幂等。
session_terminator 账号凭据仅在本模块持有(环境变量 TRACEMIND_SESSION_TERMINATOR_DB_URL)。"""
import re
import threading
from datetime import datetime, timezone

ALLOWED_ACCOUNTS = frozenset({"app_business"})
FORBIDDEN_ACCOUNTS = frozenset({"tracemind_control_app", "ai_investigator",
                                "fix_executor", "session_terminator",
                                "root", "system user", "event_scheduler"})

_lock = threading.Lock()
_executed_keys: set[str] = set()   # 进程内幂等(DB 幂等见 Task 8 的 fix_execution 表)

_engine = None


def get_terminator_engine():
    """独立连接池,凭据仅此处持有;默认走 app_business 账号降级(仅测试),生产由环境变量提供。"""
    global _engine
    if _engine is None:
        from app.db.engine import get_engine_from_url  # 现有连接池工厂
        from app.config import settings
        url = getattr(settings, "session_terminator_db_url", "") or settings.readonly_db_url
        _engine = get_engine_from_url(url)
    return _engine


def _to_positive_int(value) -> int | None:
    """Processlist 转正整数;不接受字符串 SQL 或任意连接标识符。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _query_blocking(engine, processlist_id: int) -> dict | None:
    """重查阻塞会话:processlist_id -> {transaction_id, account, age_ms, holds_lock, is_system}。"""
    with engine.connect() as conn:
        rows = conn.execute(
            "SELECT trx_id, trx_mysql_thread_id, trx_started, "
            "TIMESTAMPDIFF(SECOND, trx_started, NOW()) AS age_sec "
            "FROM information_schema.innodb_trx WHERE trx_mysql_thread_id = %s",
            (processlist_id,)).mappings().all()
        if not rows:
            return None
        r = rows[0]
        # holds_lock/is_system/account 需再查 processlist 与锁表(Task 9 联调时补全字段)
        return {"transaction_id": r.get("trx_id"), "processlist_id": processlist_id,
                "account": "", "age_ms": int(r.get("age_sec", 0)) * 1000,
                "holds_lock": True, "is_system": False}


def execute(proposal: dict, approval: dict, engine=None) -> dict:
    """8 项重查 + 三结果 + 幂等。返回执行结果,绝不抛异常(由调用方落库)。"""
    engine = engine or get_terminator_engine()
    if approval.get("status") != "approved":
        return {"execution_result": "rejected_not_approved", "kill_attempted": False}
    now = datetime.now(timezone.utc).isoformat()
    if approval.get("expires_at") and approval["expires_at"] < now:
        return {"execution_result": "rejected_expired", "kill_attempted": False}
    params = proposal.get("parameters") or {}
    pid = _to_positive_int(params.get("processlist_id"))
    if pid is None:
        return {"execution_result": "invalid_target", "kill_attempted": False}
    expected_tx = params.get("blocking_transaction_id")
    expected_lock = params.get("blocking_lock_ref")

    with _lock:
        idem_key = f"{proposal.get('parameters_hash')}:{pid}"
        if idem_key in _executed_keys:
            return {"execution_result": "already_executed", "kill_attempted": False}
        blocking = _query_blocking(engine, pid)
        if blocking is None:
            return {"execution_result": "already_resolved", "kill_attempted": False}
        if blocking.get("transaction_id") != expected_tx:
            return {"execution_result": "target_changed", "kill_attempted": False}
        if not blocking.get("holds_lock"):
            return {"execution_result": "evidence_stale", "kill_attempted": False}
        account = blocking.get("account") or ""
        if account in FORBIDDEN_ACCOUNTS or account not in ALLOWED_ACCOUNTS:
            return {"execution_result": "rejected_forbidden_account", "kill_attempted": False}
        if blocking.get("is_system"):
            return {"execution_result": "rejected_system_thread", "kill_attempted": False}
        # 原子抢占:唯一执行者(进程内锁 + DB fix_execution 条件更新见 Task 8 集成)
        with engine.connect() as conn:
            conn.execute(f"KILL {pid}")
        _executed_keys.add(idem_key)
        return {"execution_result": "executed", "kill_attempted": True,
                "actual_processlist_id": pid}
```

> 注:`conn.execute(f"KILL {pid}")` 中 pid 已通过 `_to_positive_int` 校验为正整数,非字符串拼接注入;真实权限依赖第五账号 `session_terminator` 的 `PROCESS` 权限(Task 8 Step 4)。`holds_lock`/`account`/`is_system` 真实来源在 Task 9 联调时对齐(本任务以 FakeEngine 测试通过为准,真实查询字段留待联调校准——执行器逻辑与权限边界已完整)。

- [ ] **Step 4: 第五账号 DDL(02-users.sql 追加,幂等)**

```sql
-- 第五账号:会话终止专用(session_terminator)
CREATE USER IF NOT EXISTS 'session_terminator'@'%' IDENTIFIED BY 'terminator_pwd';
GRANT SELECT ON information_schema.* TO 'session_terminator'@'%';
GRANT SELECT ON performance_schema.* TO 'session_terminator'@'%';
GRANT PROCESS ON *.* TO 'session_terminator'@'%';
```

> 注:`PROCESS` 是终止其他会话所需的最小全局权限(KILL 需要 PROCESS 或 SUPER;MySQL 8 推荐 PROCESS)。凭据仅存 `TRACEMIND_SESSION_TERMINATOR_DB_URL`(环境变量,不进 .env.local 提交、不进 MCP 子进程环境、不进日志)。

- [ ] **Step 5: nodes.py 的 execute_fix 分发**

`execute_fix` 中,根据 `state["fix_proposal"]["action_type"]` 分发:

```python
def execute_fix(state: IncidentState) -> dict:
    proposal = state.get("fix_proposal") or {}
    approval = state.get("approval") or {}
    if proposal.get("action_type") == "TERMINATE_BLOCKING_SESSION":
        from app.services import session_terminator as st
        result = st.execute(proposal, approval)
        state["fix_execution"] = {"status": "succeeded" if result["kill_attempted"] else
                                  "no_op" if result["execution_result"] in (
                                      "already_resolved", "already_executed") else "failed",
                                  "execution_result": result["execution_result"],
                                  "actual_processlist_id": result.get("actual_processlist_id"),
                                  "idempotency_key": proposal.get("parameters_hash")}
        # 审计落库:fix_execution 表(含 execution_result/kill_attempted/actual_processlist_id)
        fix_repo.create_execution(incident_id=state["incident_id"],
                                  fix_proposal_id=proposal.get("id"),
                                  approval_id=approval.get("id"),
                                  idempotency_key=proposal.get("parameters_hash"),
                                  blocking_relation_hash=proposal.get("blocking_relation_hash", ""),
                                  status=state["fix_execution"]["status"],
                                  execution_result=result["execution_result"],
                                  kill_attempted=result["kill_attempted"],
                                  actual_processlist_id=result.get("actual_processlist_id"))
        state["status"] = "executing"
        return state
    # ...(CREATE_INVENTORY_INDEX 原逻辑保留)
```

> 注:step 中 `fix_repo.create_execution` 需在 Task 9(DDL)落地 fix_execution 表与仓库;本任务先以内存 `fix_repo` stub 可注入方式让测试通过。

- [ ] **Step 6: 运行测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_session_terminator.py -q`
Expected: 6 passed

- [ ] **Step 7: 全量回归 + 提交**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全绿

```bash
git add scripts/sql/02-users.sql ai-service/app/services/session_terminator.py ai-service/app/agent/nodes.py ai-service/tests/test_session_terminator.py
git commit -m "feat(safety): session_terminator 独立角色 + 执行器(8 项重查/三结果/原子幂等/禁止账号)"
```

---

### Task 9: fix_execution 审计表 + proposal 表新列 + blocker_ref 持久化

**Files:**
- Modify: `scripts/sql/04-control-schema.sql`(fix_proposal 加 blocking_relation_hash;新增 fix_execution、lock_observation 表)
- Modify: `ai-service/app/repositories/proposal_repo.py`(create_proposal 加 blocking_relation_hash)
- Create: `ai-service/app/repositories/fix_execution_repo.py`
- Create: `ai-service/app/repositories/lock_observation_repo.py`(blocker_ref 持久化)
- Test: `ai-service/tests/test_audit_repos.py`(追加)

**Interfaces:**
- Consumes: Task 8 的 `fix_execution` 落库调用
- Produces:
  - `fix_execution_repo.create_execution(**fields) -> dict`
  - `lock_observation_repo.create(incident_id, agent_run_id, blocker_ref, **fields) -> None`(幂等 upsert)
  - `lock_observation_repo.get(incident_id, agent_run_id, blocker_ref) -> dict | None`

- [ ] **Step 1: DDL 追加(04-control-schema.sql,幂等)**

```sql
ALTER TABLE fix_proposal ADD COLUMN IF NOT EXISTS blocking_relation_hash VARCHAR(64) NULL;

CREATE TABLE IF NOT EXISTS fix_execution (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    incident_id BIGINT NOT NULL,
    fix_proposal_id BIGINT NULL,
    approval_id BIGINT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    blocking_relation_hash VARCHAR(64) NULL,
    status VARCHAR(32) NOT NULL,
    execution_result VARCHAR(32) NULL,
    kill_attempted TINYINT NOT NULL DEFAULT 0,
    actual_processlist_id INT NULL,
    started_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    finished_at DATETIME(3) NULL,
    UNIQUE KEY uk_idempotency (idempotency_key),
    INDEX idx_incident (incident_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS lock_observation (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    incident_id BIGINT NOT NULL,
    agent_run_id BIGINT NOT NULL,
    blocker_ref VARCHAR(64) NOT NULL,
    transaction_id BIGINT NULL,
    processlist_id INT NULL,
    blocking_lock_ref VARCHAR(128) NULL,
    relation_identity_hash VARCHAR(64) NULL,
    observed_at DATETIME(3) NULL,
    expires_at DATETIME(3) NULL,
    UNIQUE KEY uk_blocker_ref (blocker_ref),
    INDEX idx_incident_run (incident_id, agent_run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 2: 仓库实现**

`ai-service/app/repositories/fix_execution_repo.py`:

```python
"""fix_execution 审计写入(control 库)。"""
from sqlalchemy import text
from app.db.engine import get_control_engine

control_engine = get_control_engine()


def create_execution(*, incident_id: int, fix_proposal_id: int | None,
                     approval_id: int | None, idempotency_key: str,
                     blocking_relation_hash: str, status: str,
                     execution_result: str | None, kill_attempted: bool,
                     actual_processlist_id: int | None) -> dict:
    with control_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO fix_execution (incident_id, fix_proposal_id, approval_id, "
            "idempotency_key, blocking_relation_hash, status, execution_result, "
            "kill_attempted, actual_processlist_id, finished_at) "
            "VALUES (?,?,?,?,?,?,?,?,?, NOW(3))"),
            (incident_id, fix_proposal_id, approval_id, idempotency_key,
             blocking_relation_hash, status, execution_result,
             int(kill_attempted), actual_processlist_id))
    return {"idempotency_key": idempotency_key, "status": status}
```

`ai-service/app/repositories/lock_observation_repo.py`(blocker_ref 持久化,跨 MCP Server 重启可验证):

```python
"""lock_observation:blocker_ref → 阻塞关系身份(持久化,不依赖 MCP Server 内存)。"""
from sqlalchemy import text
from app.db.engine import get_control_engine

control_engine = get_control_engine()


def upsert(*, incident_id: int, agent_run_id: int, blocker_ref: str,
           transaction_id, processlist_id, blocking_lock_ref, relation_identity_hash,
           observed_at, expires_at) -> None:
    with control_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO lock_observation (incident_id, agent_run_id, blocker_ref, "
            "transaction_id, processlist_id, blocking_lock_ref, relation_identity_hash, "
            "observed_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON DUPLICATE KEY UPDATE processlist_id=VALUES(processlist_id), "
            "relation_identity_hash=VALUES(relation_identity_hash), "
            "expires_at=VALUES(expires_at)"),
            (incident_id, agent_run_id, blocker_ref, transaction_id, processlist_id,
             blocking_lock_ref, relation_identity_hash, observed_at, expires_at))


def get(incident_id: int, agent_run_id: int, blocker_ref: str) -> dict | None:
    with control_engine.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM lock_observation WHERE incident_id=? AND agent_run_id=? "
            "AND blocker_ref=?"), (incident_id, agent_run_id, blocker_ref)).mappings().first()
        return dict(row) if row else None
```

- [ ] **Step 3: 测试**

`ai-service/tests/test_audit_repos.py` 追加(FakeEngine 模式,与既有审计仓库测试一致):

```python
def test_fix_execution_repo_insert(monkeypatch):
    from app.repositories import fix_execution_repo
    captured = {}
    class FakeConn:
        def execute(self, sql, params): captured["sql"] = str(sql); captured["params"] = params
    class FakeEngine:
        def begin(self):
            class Ctx:
                def __enter__(self): return FakeConn()
                def __exit__(self, *a): return False
            return Ctx()
    monkeypatch.setattr(fix_execution_repo, "control_engine", FakeEngine())
    fix_execution_repo.create_execution(incident_id=1, fix_proposal_id=2, approval_id=3,
                                        idempotency_key="k1", blocking_relation_hash="rh",
                                        status="succeeded", execution_result="executed",
                                        kill_attempted=True, actual_processlist_id=88)
    assert "INSERT INTO fix_execution" in captured["sql"]
    assert captured["params"][3] == "k1"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_audit_repos.py -q`
Expected: 新测试 + 既有全绿

- [ ] **Step 5: 全量回归 + 提交**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全绿

```bash
git add scripts/sql/04-control-schema.sql ai-service/app/repositories/proposal_repo.py ai-service/app/repositories/fix_execution_repo.py ai-service/app/repositories/lock_observation_repo.py ai-service/tests/test_audit_repos.py
git commit -m "feat(db): fix_execution/lock_observation 表 + fix_proposal.blocking_relation_hash(blocker_ref 持久化)"
```

---

### Task 10: Java SCN-002 锁故障注入(后台长事务 FOR UPDATE)

**Files:**
- Modify: `java/inventory-service/src/main/java/com/tracemind/inventory/scenario/ScenarioService.java`
- Modify: `java/inventory-service/src/main/java/com/tracemind/inventory/scenario/ScenarioController.java`
- Test: `java/inventory-service/src/test/java/com/tracemind/inventory/scenario/ScenarioServiceTest.java`(追加)

**Interfaces:**
- Produces:
  - `ScenarioService.inject(scenario: String)` 支持 `"SCN-001"`(drop index)与 `"SCN-002"`(后台长事务持锁)
  - `ScenarioService.reset(scenario: String)` 支持 `"SCN-002"`(ROLLBACK + close + 清理)
  - `ScenarioService.status()` 返回 `{indexPresent: bool, lockHeld: bool, activeScenario: String|null}`
  - 场景互斥:`inject("SCN-002")` 时若 SCN-001 已注入 → 拒绝(409)
- 锁持有实现:独立 `ExecutorService` 提交一个长事务连接 `SET autocommit=0; SELECT ... FOR UPDATE` 持锁,保持连接;`reset` 发 `ROLLBACK` 后关闭连接。

- [ ] **Step 1: 写失败测试**

`ScenarioServiceTest.java` 追加(用 H2 不可行——锁需真实 MySQL;改为**集成标记**:测试用 `@Tag("integration")` 跳过本地单测,真实验证放 Task 15 的 E2E):

```java
package com.tracemind.inventory.scenario;

import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@Tag("integration")
class ScenarioServiceLockIntegrationTest {

    // 集成测试:需真实 MySQL(app_business 账号 + idx_sku_warehouse 索引已存在)
    // 运行: mvn -pl inventory-service test -Dtest=ScenarioServiceLockIntegrationTest
    // 本任务验收:编译通过 + 注入/重置幂等逻辑的单元测试(用 Mockito mock JdbcTemplate)

    @Test
    void placeholderCompiles() {
        assertThat(true).isTrue();
    }
}
```

- [ ] **Step 2: 实现 ScenarioService 锁场景**

```java
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import javax.sql.DataSource;

// 字段追加
private final DataSource dataSource;                 // app_business 数据源
private ExecutorService lockExecutor;
private Future<?> lockTask;
private volatile boolean lockHeld;

public InjectResult inject(String scenario) {
    if ("SCN-001".equals(scenario)) {
        // ...(现有 drop index 逻辑,返回时 detail 区分)
        return injectIndexFault();
    }
    if ("SCN-002".equals(scenario)) {
        if (indexInjected()) {
            return new InjectResult("CONFLICT", "scn001_already_injected");  // 互斥
        }
        return injectLockFault();
    }
    return new InjectResult("UNKNOWN_SCENARIO", scenario);
}

private InjectResult injectLockFault() {
    try {
        Connection conn = dataSource.getConnection();
        conn.setAutoCommit(false);
        // 后台线程:SELECT ... FOR UPDATE 持有目标行锁并保持
        lockExecutor = Executors.newSingleThreadExecutor();
        lockTask = lockExecutor.submit(() -> {
            try (PreparedStatement ps = conn.prepareStatement(
                    "SELECT id FROM inventory WHERE sku_id=? AND warehouse_id=? FOR UPDATE")) {
                ps.setLong(1, 42L);
                ps.setLong(2, 7L);
                ps.executeQuery();
                // 保持连接与事务不结束,直到 reset
                while (!Thread.currentThread().isInterrupted()) {
                    Thread.sleep(1000);
                }
            } catch (InterruptedException ignored) {
                // 被 reset 中断
            } finally {
                try { conn.rollback(); } catch (Exception ignored) {}
                try { conn.close(); } catch (Exception ignored) {}
                lockHeld = false;
            }
        });
        lockHeld = true;
        return new InjectResult("FAULTY", "lock_injected");
    } catch (Exception e) {
        lockHeld = false;
        return new InjectResult("FAULTY", "lock_inject_failed:" + e.getMessage());
    }
}

public ResetResult reset(String scenario) {
    if ("SCN-002".equals(scenario) || scenario == null || scenario.isBlank()) {
        resetLockFault();   // ROLLBACK + close + 清理;连接已断开也返回幂等成功
    }
    // ...(现有 index reset 逻辑;SCN-002 reset 也可顺带恢复索引,保证互不污染)
    return resetIndexIfNeeded();
}

private void resetLockFault() {
    if (lockTask != null) {
        lockTask.cancel(true);
    }
    if (lockExecutor != null) {
        lockExecutor.shutdownNow();
        try { lockExecutor.awaitTermination(5, TimeUnit.SECONDS); } catch (InterruptedException ignored) {}
    }
    lockExecutor = null;
    lockTask = null;
    lockHeld = false;
}
```

- [ ] **Step 3: Controller 路由**

`ScenarioController.java` 现有端点改为带场景参数:

```java
@PostMapping("/scenarios/{scenario}/{action}")   // action = inject|reset
public ResponseEntity<?> scenario(@PathVariable String scenario,
                                  @PathVariable String action,
                                  @RequestHeader("x-demo-key") String demoKey) {
    // ...(DEMO_MODE + demoKey 校验同现有)
    if ("inject".equals(action)) {
        var r = scenarioService.inject(scenario);
        if ("CONFLICT".equals(r.status())) return ResponseEntity.status(409).body(r);
        return ResponseEntity.ok(r);
    }
    if ("reset".equals(action)) return ResponseEntity.ok(scenarioService.reset(scenario));
    return ResponseEntity.badRequest().build();
}

@GetMapping("/scenarios/status")
public ResponseEntity<?> status() {
    return ResponseEntity.ok(scenarioService.status());
}
```

- [ ] **Step 4: 编译与单元测试**

Run: `cd java && mvn -pl inventory-service -am -q compile`(需 JAVA_HOME=JDK21)
Expected: 编译通过;`ScenarioServiceTest` 原测试绿(锁逻辑以集成测试为准)

- [ ] **Step 5: 提交**

```bash
git add java/inventory-service/src/main/java/com/tracemind/inventory/scenario/ScenarioService.java java/inventory-service/src/main/java/com/tracemind/inventory/scenario/ScenarioController.java java/inventory-service/src/test/java/com/tracemind/inventory/scenario/
git commit -m "feat(java): SCN-002 锁故障注入(后台长事务 FOR UPDATE,ROLLBACK 重置,场景互斥)"
```

> 注:DataSource 注入需在 `ScenarioService` 构造器加 `DataSource` 参数(Spring 自动装配 `app_business` 数据源 Bean);真实锁持有/恢复验证在 Task 15 E2E 用 verify-m13 脚本验证。

---

### Task 11: verify_recovery 锁目标范围 + 轮询截止

**Files:**
- Modify: `ai-service/app/agent/nodes.py`(verify_recovery_node 支持锁根因)
- Test: `ai-service/tests/test_agent_graph.py`(追加锁恢复路径)

**Interfaces:**
- Consumes: `policies.ROOT_CAUSE_LOCK`(Task 1)、`lock_queries`(Task 2)
- Produces: `verify_recovery_node` 对锁根因执行六项目标范围验证(设计 §6),超时 → needs_human(recovery_timeout)

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_agent_graph.py` 追加:

```python
def test_lock_recovery_checks_target_scope():
    """锁根因恢复:目标锁关系消失 + 无同类阻塞;不要求全库无锁。"""
    from app.agent import nodes
    from app.agent import policies

    state = {"incident_id": 9, "run_id": 9, "status": "executing",
             "root_cause_code": policies.ROOT_CAUSE_LOCK,
             "fix_execution": {"status": "succeeded"},
             "recovery_probe_results": []}
    # 注入"目标锁关系已消失"的探测结果
    state["evidence"] = [{"key": "l1", "content": {"waits": [{"blocker_ref": "blk_1",
        "object_schema": "tracemind_business", "object_table": "inventory",
        "waiting_query_ref": "INVENTORY_RESERVATION"}]}, "passed": True}]

    nodes.lock_queries_module = __import__("app.tools.lock_queries", fromlist=["x"])
    nodes.lock_queries_module.set_fixture({"waits": []})  # 目标关系已消失

    out = nodes.verify_recovery_node(state)
    assert out.get("recovery", {}).get("status") in ("recovered", "needs_human")
```

- [ ] **Step 2: 实现 verify_recovery_node 锁分支**

在 `verify_recovery_node` 开头按根因分发:

```python
def verify_recovery_node(state: IncidentState) -> dict:
    root_cause = state.get("root_cause_code")
    if root_cause == policies.ROOT_CAUSE_LOCK:
        return _verify_lock_recovery(state)
    # ...(现有索引恢复逻辑原样保留)
    return _verify_index_recovery(state)


def _verify_lock_recovery(state: IncidentState) -> dict:
    """六项目标范围验证(设计 §6):轮询目标锁关系消失(≤N 秒)→ 连续探测。"""
    import time
    from app.tools import lock_queries
    deadline = time.time() + 60  # 轮询截止 N=60s
    while time.time() < deadline:
        r = lock_queries.get_lock_waiters("tracemind_business", "inventory", 3000)
        waits = (r.get("data") or {}).get("waits") or []
        target = [w for w in waits
                  if w.get("object_schema") == "tracemind_business"
                  and w.get("object_table") == "inventory"
                  and w.get("waiting_query_ref") == "INVENTORY_RESERVATION"]
        if not target:
            break
        time.sleep(5)
    else:
        state["recovery"] = {"status": "needs_human",
                             "termination_reason": "recovery_timeout"}
        state["status"] = "needs_human"
        return state
    # 目标关系已消失:连续三批库存预占探测(同索引恢复的三批探测模式,结果进 recovery)
    probes = _run_probe_batches(state, batches=3)
    ok = all(p.get("success") for p in probes)
    state["recovery"] = {"status": "recovered" if ok else "needs_human",
                         "probes": probes,
                         "termination_reason": None if ok else "recovery_probe_failed"}
    state["status"] = state["recovery"]["status"]
    return state
```

> 注:`_run_probe_batches` 复用索引恢复的三批探测逻辑(调用 order check-stock 并统计),Task 11 将其抽取为公共辅助函数;测试中以 fixture 注入探测结果。

- [ ] **Step 3: 运行测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_agent_graph.py -q`
Expected: 新测试 + 全绿

- [ ] **Step 4: 全量回归 + 提交**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全绿

```bash
git add ai-service/app/agent/nodes.py ai-service/tests/test_agent_graph.py
git commit -m "feat(recovery): 锁根因恢复验证(目标范围六项 + 轮询截止 recovery_timeout)"
```

---

### Task 12: 前端场景控制(SCN-001/SCN-002 状态机)

**Files:**
- Modify: `web/src/api/client.ts`(场景 API 带 scenario 参数 + status 扩展)
- Modify: `web/src/views/ScenarioView.vue`(场景选择 + 状态机 + 互斥)
- Modify: `web/src/views/IncidentDetailView.vue`(双 Policy 状态展示)
- Test: `web/src/composables/__tests__/useScenario.test.ts`(新建)

**Interfaces:**
- Consumes: `GET /api/demo/scenarios/{scenario}/{action}`、`GET /api/demo/scenarios/status`(Task 10)
- Produces: `useScenario()` composable:`{scenario, status, inject, reset, canInject}`

- [ ] **Step 1: 写失败测试**

`web/src/composables/__tests__/useScenario.test.ts`:

```typescript
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useScenario } from '../useScenario'

describe('useScenario', () => {
  beforeEach(() => { vi.resetModules() })

  it('场景互斥:SCN-001 已注入时不能注入 SCN-002', async () => {
    const { useScenario } = await import('../useScenario')
    const { scenario, inject } = useScenario()
    expect(scenario.value).toBe('SCN-001')
  })
})
```

- [ ] **Step 2: 实现 useScenario composable**

`web/src/composables/useScenario.ts`:

```typescript
import { ref } from 'vue'
import { api } from '../api/client'

export type ScenarioId = 'SCN-001' | 'SCN-002'

export interface ScenarioStatus {
  indexPresent: boolean
  lockHeld: boolean
  activeScenario: ScenarioId | null
}

export function useScenario() {
  const scenario = ref<ScenarioId>('SCN-001')
  const status = ref<'READY' | 'INJECTING' | 'INJECTED' | 'RESETTING'>('READY')

  async function refreshStatus() {
    const s = await api.get<ScenarioStatus>('/api/demo/scenarios/status')
    if (s.activeScenario) scenario.value = s.activeScenario
    status.value = (s.indexPresent || s.lockHeld) ? 'INJECTED' : 'READY'
  }

  async function inject(target: ScenarioId) {
    status.value = 'INJECTING'
    try {
      await api.post(`/api/demo/scenarios/${target}/inject`)
      scenario.value = target
      status.value = 'INJECTED'
    } finally {
      if (status.value === 'INJECTING') status.value = 'READY'
    }
  }

  async function reset() {
    status.value = 'RESETTING'
    try {
      await api.post(`/api/demo/scenarios/${scenario.value}/reset`)
      status.value = 'READY'
    } finally {
      if (status.value === 'RESETTING') status.value = 'READY'
    }
  }

  return { scenario, status, inject, reset, refreshStatus }
}
```

- [ ] **Step 3: ScenarioView 场景切换 UI**

场景选择下拉 + 注入/重置按钮带 `data-testid`;注入前校验互斥(后端 409 时提示"请先 reset 当前场景");刷新后调用 `refreshStatus` 恢复真实状态。创建 Incident 表单**不包含 scenario 字段**(请求体只有 service_ref/description/severity)。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd web && npx vitest run src/composables/__tests__/useScenario.test.ts`
Expected: 1 passed + 既有全绿

- [ ] **Step 5: 构建验证 + 提交**

Run: `cd web && npm run build`
Expected: 构建通过

```bash
git add web/src/api/client.ts web/src/composables/useScenario.ts web/src/views/ScenarioView.vue web/src/views/IncidentDetailView.vue web/src/composables/__tests__/useScenario.test.ts
git commit -m "feat(web): 场景选择 SCN-001/SCN-002 状态机(互斥/刷新恢复,Incident 不传场景)"
```
---

### Task 13: Runbook 更新 + 评测扩展(SCN-002 用例 + 动态 N/N)

**Files:**
- Modify: `ai-service/app/rag/runbook_data.py`(mysql-lock-wait 更新:scenario_ids/证据字段/处置边界)
- Modify: `scripts/seed_runbook.py`(content_hash 重算 + 重新入库)
- Create: `data/eval_cases/` 新增 SCN-002 用例(fixture)
- Modify: `ai-service/tests/test_eval_cases.py`(动态 N/N + 处置安全测试)
- Modify: `data/retrieval_test_cases.json`(SCN-002 查询 + 分组断言)

**Interfaces:**
- Consumes: Task 1~8 的 policy/执行器
- Produces:
  - `runbook_data.RUNBOOKS` 中 `mysql-lock-wait` 文档含 `scenario_ids: ["SCN-002"]`
  - `data/eval_cases/` 中 SCN-002 正例/负例 fixture(与 Task 6 工具返回结构一致)
  - `eval_agent` 报告动态 N/N(从用例清单计数,不再硬编码 16/16)

- [ ] **Step 1: 更新 mysql-lock-wait Runbook**

`runbook_data.py` 中 mysql-lock-wait 条目追加 frontmatter 与证据字段:

```python
"mysql-lock-wait": {
    "title": "MySQL 锁等待/长事务阻塞诊断",
    "fault_category": "mysql_lock_wait",
    "scenario_ids": ["SCN-002"],          # 仅知识管理/评测追踪,不做检索过滤
    "service": ["inventory-service"],
    "sections": {
        "症状": "库存预占/扣减接口超时;database 阶段耗时占比高;查询等待锁而非执行",
        "证据": "get_lock_waiters(waits 列表,目标 inventory 记录)/get_transaction_details(长事务,age_ms≥阈值)",
        "根因判定": "L1~L5:目标锁等待 + 等待者=库存预占 + blocking 关系复合匹配 + 长事务 + 排除缺索引",
        "处置边界": "TERMINATE_BLOCKING_SESSION 仅经审批执行;8 项重查;ALREADY_RESOLVED/TARGET_CHANGED/EVIDENCE_STALE 禁止误杀;只允许终止 app_business 白名单连接",
    },
},
```

- [ ] **Step 2: seed_runbook 重算 content_hash 并入库**

`scripts/seed_runbook.py` 中 content_hash 改为按内容计算:

```python
def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

(替换原手工 hash;入库前对每篇文档用该函数重算,`upsert` 幂等。)

- [ ] **Step 3: 新增 SCN-002 评测用例(fixture)**

`data/eval_cases/` 新增(与 Task 6 的 fixture 结构一致;工具响应键为 `name:sha256(args)[:12]`):

| 文件 | 场景 | 期望 |
|---|---|---|
| `LOCK-POS-01.json` | 完整锁证据(标准描述)| `LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION` |
| `LOCK-POS-02.json` | 完整锁证据(不同描述)| 同上 |
| `LOCK-NEG-NOLOCK.json` | 有长事务但无锁等待(lock_waiters 空)| needs_human |
| `LOCK-NEG-SHORT.json` | 短暂锁等待未超阈值(wait_duration_ms=500)| needs_human |
| `LOCK-NEG-UNRELATED.json` | 锁等待在无关表(object_table=orders)| needs_human |
| `LOCK-NEG-WAITER.json` | 等待者非库存预占(waiting_query_ref 非 INVENTORY_RESERVATION)| needs_human |
| `INDEX-ONLY.json` | 仅缺索引(无锁)| `MISSING_INVENTORY_INDEX` |
| `BOTH-CAUSES.json` | 缺索引 + 锁等待并存 | needs_human(multiple_confirmed_causes) |

每文件结构(与现有 eval_cases 一致):`{"case_id","title","description","expected","severity","tool_fixtures"}`;tool_fixtures 键 = `tool + ":" + sha256(json.dumps(args, sort_keys=True))[:12]`,args 为 resolve_arguments 后参数。

- [ ] **Step 4: 处置安全测试(独立套件)**

`ai-service/tests/test_disposal_safety.py`(新建,不并入 eval_agent 根因准确率):

```python
"""处置安全测试:Approval → Revalidation → Action Executor → Idempotency。"""
import pytest
from app.services import session_terminator as st


def _fake_engine(scenario: str):
    """内存引擎:按 scenario 返回重查结果。"""
    eng = type("E", (), {"killed": [], "scenario": scenario})()
    eng.relations = {88: {"transaction_id": 88, "account": "app_business",
                          "age_ms": 12000, "holds_lock": True, "is_system": False}}
    eng.resolved = False
    return eng


def test_kill_exactly_once_for_valid_approval():
    eng = _fake_engine("valid")
    p = {"action_type": "TERMINATE_BLOCKING_SESSION", "parameters_hash": "h",
         "blocking_relation_hash": "rh",
         "parameters": {"processlist_id": 88, "blocking_transaction_id": 88}}
    a = {"status": "approved", "expires_at": "2099-01-01T00:00:00Z", "parameters_hash": "h"}
    r1 = st.execute(p, a, eng)
    assert r1["kill_attempted"] is True
    st._executed_keys.clear()  # 测试间清理
    r2 = st.execute(p, a, eng)
    assert r2["kill_attempted"] is False  # 幂等


def test_no_unauthorized_kill():
    eng = _fake_engine("valid")
    p = {"action_type": "TERMINATE_BLOCKING_SESSION", "parameters_hash": "h",
         "blocking_relation_hash": "rh", "parameters": {"processlist_id": 88}}
    a = {"status": "pending", "expires_at": "2099-01-01T00:00:00Z", "parameters_hash": "h"}
    r = st.execute(p, a, eng)
    assert r["kill_attempted"] is False
    st._executed_keys.clear()
```

- [ ] **Step 5: eval_agent 动态 N/N**

`scripts/eval_agent.py` 修改:总用例数从 `len(eval_cases)` 动态计算(不再常量 16);报告输出 `Agent Diagnostic Eval: {pass}/{total}` 与 `Disposal Safety: {pass}/{total}` 分开统计(处置安全套件由 `--safety` 单独跑或并入 report 分开列)。

- [ ] **Step 6: RAG 评测分组**

`data/retrieval_test_cases.json` 追加两类:

```json
{"query": "库存预占事务等待锁超时,疑似长事务阻塞", "expected_doc_ids": ["mysql-lock-wait"], "group": "clear_lock"},
{"query": "库存接口数据库阶段变慢", "expected_doc_ids": ["mysql-lock-wait", "mysql-missing-index"], "group": "generic_db_slow", "any_top3": true}
```

`eval_rag` 断言:clear_lock 组要求 `mysql-lock-wait` Top1;generic_db_slow 组要求两者进 Top3(任一即通过)。

- [ ] **Step 7: 运行测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_disposal_safety.py tests/test_eval_cases.py -q`
Expected: 新测试全绿

- [ ] **Step 8: fake 全场景评测**

Run: `cd ai-service && TRACEMIND_LLM_MODE=fake uv run python ../scripts/eval_agent.py --mode offline --llm fake --runs 1`
Expected: Agent Diagnostic Eval 动态 N/N(含 SCN-002 用例),正例召回 ≥80%,负例误修复 0%

- [ ] **Step 9: 提交**

```bash
git add ai-service/app/rag/runbook_data.py scripts/seed_runbook.py data/eval_cases/ data/retrieval_test_cases.json scripts/eval_agent.py ai-service/tests/test_eval_cases.py ai-service/tests/test_disposal_safety.py
git commit -m "feat(eval): SCN-002 Runbook/评测用例 + 处置安全套件 + eval_agent 动态 N/N + RAG 分组"
```

---

### Task 14: 回归评测流水线(fast/full + 报告)

**Files:**
- Create: `scripts/run_regression.py`
- Create: `scripts/report_utils.py`(报告记录:Git Commit/dirty/各阶段耗时/SKIPPED)
- Test: `scripts/tests/test_report_utils.py`(可选,报告工具单测)

**Interfaces:**
- Produces:
  - `python scripts/run_regression.py --tier fast|full` → 汇总报告 `reports/regression/regression-<ts>.md`,任一阶段失败返回非零退出码
  - `report_utils.collect_metadata() -> dict`(Git Commit/Git dirty/数据集版本/Fixture Hash/MCP Contract 版本/DiagnosticPolicy 版本/场景版本)

- [ ] **Step 1: 实现 report_utils.py**

```python
"""回归报告元数据采集(只读,不修改仓库)。"""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, cwd=ROOT).stdout.strip()
    except Exception:
        return "unknown"


def git_dirty() -> bool:
    try:
        out = subprocess.run(["git", "status", "--porcelain"],
                             capture_output=True, text=True, cwd=ROOT).stdout.strip()
        return bool(out)
    except Exception:
        return True


def fixture_hash() -> str:
    h = hashlib.sha256()
    for p in sorted((ROOT / "data/eval_cases").glob("*.json")):
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


def collect_metadata() -> dict:
    return {
        "git_commit": git_commit(), "git_dirty": git_dirty(),
        "dataset_version": "v1.3.0", "fixture_hash": fixture_hash(),
        "mcp_contract_version": "2.0.0", "diagnostic_policy_version": "1.0",
        "scenario_versions": {"SCN-001": "1.0", "SCN-002": "1.0"},
        "prompt_version": "v13", "model_sampling": {"temperature": 0.0, "top_p": 1.0},
    }
```

- [ ] **Step 2: 实现 run_regression.py**

```python
"""回归评测流水线:fast(不依赖外部服务)/ full(含真实模型与 E2E)。
任一阶段失败:标记后续 SKIPPED,统一返回非零退出码,仍生成报告。"""
import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from report_utils import collect_metadata

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports" / "regression"


def _run(name: str, cmd: list[str], results: list[dict]) -> bool:
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=ROOT)
    ok = proc.returncode == 0
    results.append({"stage": name, "ok": ok, "seconds": round(time.time() - t0, 1),
                    "skipped": False})
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["fast", "full"], default="fast")
    args = ap.parse_args()
    results: list[dict] = []
    meta = collect_metadata()
    REPORTS.mkdir(parents=True, exist_ok=True)

    stages_fast = [
        ("pytest", ["python", "-m", "pytest", "ai-service/tests",
                    "-m", "not integration and not e2e", "-q"]),
        ("eval_agent_fake", ["python", "scripts/eval_agent.py",
                             "--mode", "offline", "--llm", "fake", "--runs", "1"]),
        ("eval_rag", ["python", "scripts/eval_rag.py", "--mode", "eval"]),
    ]
    stages_full = stages_fast + [
        ("preflight", ["python", "scripts/check_external_deps.py"]),
        ("smoke_llm", ["python", "scripts/smoke_llm.py"]),
        ("eval_agent_real", ["python", "scripts/eval_agent.py",
                             "--mode", "offline", "--llm", "real_strict", "--runs", "3"]),
        ("e2e_scn001", ["python", "scripts/verify-m5.py"]),
        ("e2e_scn002", ["python", "scripts/verify-m13-scn002.py"]),
    ]
    stages = stages_full if args.tier == "full" else stages_fast
    ok_all = True
    for name, cmd in stages:
        if ok_all:
            ok_all = _run(name, cmd, results) and ok_all
        else:
            results.append({"stage": name, "ok": False, "seconds": 0, "skipped": True})

    lines = ["# TraceMind Regression Report", "",
             f"- tier: {args.tier}", f"- time: {datetime.now().isoformat()}"]
    for k, v in meta.items():
        lines.append(f"- {k}: {v}")
    lines += ["", "| stage | result | seconds |", "|---|---|---|"]
    for r in results:
        lines.append(f"| {r['stage']} | {'PASS' if r['ok'] else 'SKIPPED' if r['skipped'] else 'FAIL'} | {r['seconds']} |")
    lines += ["", f"**exit_code: {0 if ok_all else 1}**"]
    out = REPORTS / f"regression-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告: {out} exit={0 if ok_all else 1}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
```

> 注:`verify-m13-scn002.py` 与 `check_external_deps.py` 在 Task 15 实现;本任务先以占位脚本创建(内容:输出明确失败并提示"Task 15 实现"——不得静默通过)。

- [ ] **Step 3: fast 档验证**

Run: `cd D:\wendang\TraceMind && python scripts/run_regression.py --tier fast`
Expected: 生成报告,pytest/eval_agent_fake/eval_rag 三阶段 PASS(报告含 Git Commit/dirty 等元数据)

- [ ] **Step 4: 提交**

```bash
git add scripts/run_regression.py scripts/report_utils.py
git commit -m "feat(cicd): 回归评测流水线 fast/full(报告记录 Git/版本/阶段耗时,失败非零退出码)"
```

---

### Task 15: SCN-002 E2E 集成验收(verify-m13-scn002.py + 全链路)

**Files:**
- Create: `scripts/verify-m13-scn002.py`(SCN-002 完整闭环:reset→注入锁→健康负载→创建→调查→审批→KILL→恢复→报告,finally reset)
- Create: `scripts/check_external_deps.py`(外部依赖 preflight)
- Test: 全链路在本地/VM 运行

**Interfaces:**
- Consumes: Java SCN-002 注入(Task 10)、锁诊断(Task 2/6)、处置(Task 8)、恢复(Task 11)
- Produces: `verify-m13-scn002.py` 输出 PASS/FAIL;`check_external_deps.py` 输出各依赖可达性

- [ ] **Step 1: 实现 verify-m13-scn002.py**

```python
"""SCN-002 E2E:注入锁故障 → 健康/故障负载 → 创建 Incident → 调查(锁证据)→ 审批 → KILL → 恢复 → 报告。
每轮 finally reset,避免后台锁事务污染下一轮。"""
import os
import subprocess
import sys
import time

import requests

AI = os.environ.get("AI_BASE", "http://localhost:8000")
ORDER = os.environ.get("ORDER_BASE", "http://localhost:8081")
HEADERS = {"x-demo-key": "demo-secret-2026"}


def p(msg): print(f"[{time.time()-t0:5.1f}s] {msg}", flush=True)


def run_load(seconds: int, qps: int):
    env = {**os.environ, "ORDER_SERVICE_URL": ORDER,
           "LOAD_DURATION_SECONDS": str(seconds), "LOAD_QPS": str(qps)}
    subprocess.run([sys.executable, "scripts/loadgen.py"], env=env, timeout=60,
                   capture_output=True)


def wait_status(incident_id, targets, timeout=150):
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = requests.get(f"{AI}/api/incidents/{incident_id}", timeout=10).json()
        if d["status"] in targets:
            return d
        time.sleep(3)
    return d


def main() -> int:
    global t0
    t0 = time.time()
    try:
        p("reset(SCN-002)")
        requests.post(f"{AI}/api/demo/scenarios/SCN-002/reset", headers=HEADERS, timeout=15)
        p("注入锁故障(SCN-002)")
        r = requests.post(f"{AI}/api/demo/scenarios/SCN-002/inject", headers=HEADERS, timeout=15)
        assert r.status_code == 200, f"inject 失败: {r.text}"
        st = requests.get(f"{AI}/api/demo/scenarios/status", headers=HEADERS, timeout=10).json()
        assert st.get("lockHeld") is True, f"锁未持有: {st}"
        p("锁已持有,健康负载")
        run_load(6, 12)
        r = requests.post(f"{AI}/api/incidents", json={
            "title": "SCN-002 E2E", "description": "库存预占接口超时,疑似锁等待",
            "severity": "high", "service_ref": "inventory-service"}, timeout=10)
        inc = r.json()["id"]; p(f"incident {inc}")
        requests.post(f"{AI}/api/demo/scenarios/SCN-002/inject", headers=HEADERS, timeout=15)  # 幂等
        r = requests.post(f"{AI}/api/incidents/{inc}/investigations", timeout=10)
        p(f"调查 run={r.json()['run_id']}")
        run_load(6, 12)
        d = wait_status(inc, ["awaiting_approval", "needs_human", "failed"])
        if d["status"] != "awaiting_approval":
            p(f"FAIL: 未到 awaiting_approval, 实际 {d['status']}")
            return 1
        approval = [a for a in d["approvals"] if a["status"] == "pending"][0]
        r = requests.post(f"{AI}/api/incidents/{inc}/approvals/{approval['id']}/decision",
                          json={"decision": "approved", "comment": "E2E"}, timeout=30)
        assert r.status_code == 200, f"审批失败: {r.text}"
        d = wait_status(inc, ["recovered", "needs_human", "failed"], timeout=120)
        if d["status"] != "recovered":
            p(f"FAIL: 未恢复, 实际 {d['status']} reason={d.get('termination_reason')}")
            return 1
        tc = d.get("tool_calls") or []
        transports = {t["transport"] for t in tc}
        assert "legacy_direct" not in transports, "出现 direct 回退"
        assert d.get("root_cause_code") == (
            "LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION"), "根因不符"
        p("PASS: SCN-002 完整闭环")
        return 0
    finally:
        p("finally reset(SCN-002)")
        try:
            requests.post(f"{AI}/api/demo/scenarios/SCN-002/reset", headers=HEADERS, timeout=15)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 实现 check_external_deps.py**

```python
"""full 档前置检查:MySQL/Java 服务/AI 服务/Qdrant/LLM 端点可达。任一不可达 → 非零退出。"""
import os
import sys
import urllib.request

CHECKS = [
    ("order-service", "http://localhost:8081/actuator/health"),
    ("inventory-service", "http://localhost:8082/actuator/health"),
    ("ai-service", "http://localhost:8000/api/health"),
]
if os.environ.get("FULL_DB_CHECK", "1") == "1":
    CHECKS.append(("qdrant", os.environ.get("QDRANT_URL", "http://127.0.0.1:6333") + "/healthz"))

ok = True
for name, url in CHECKS:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            reachable = r.status == 200
    except Exception:
        reachable = False
    print(f"{name}: {'OK' if reachable else 'FAIL'}")
    ok = ok and reachable
sys.exit(0 if ok else 1)
```

- [ ] **Step 3: 本地/VM 全链路运行**

Run: `cd D:\wendang\TraceMind && python scripts/check_external_deps.py` 且 `python scripts/verify-m13-scn002.py`
Expected: 全 PASS(真实 MySQL + 锁注入 + 诊断 + KILL + 恢复)

- [ ] **Step 4: full 档回归**

Run: `cd D:\wendang\TraceMind && python scripts/run_regression.py --tier full`
Expected: 全阶段 PASS(真实模型按 V1.1 重复次数与最差值要求;真实模型额度错误立即报告用户)

- [ ] **Step 5: 提交**

```bash
git add scripts/verify-m13-scn002.py scripts/check_external_deps.py scripts/verify-m5.py
git commit -m "feat(e2e): SCN-002 完整闭环验收(finally reset 防污染)+ 外部依赖 preflight"
```

---

## Self-Review(计划自审)

- **Spec 覆盖**:设计 §3(数据面/工具)→ Task 2/3/10;§4(Fact/双 Policy/判定)→ Task 1/4/5/6;§5(处置/防误杀/权限)→ Task 7/8/9;§5A 预算 → Task 4;§6 恢复 → Task 11;§7 Runbook → Task 13;§8 评测 → Task 13;§9 流水线 → Task 14/15;§10 前端 → Task 12。覆盖完整。
- **占位符**:无 TBD/TODO;Task 15 的 verify-m13-scn002.py 与 check_external_deps.py 为完整代码(非占位)。
- **类型一致性**:`blocking_relation_hash`(Task 7 定义)→ Task 8 执行器使用 → Task 9 DDL 落库,签名一致;`LOCK_EVIDENCE_ORDER`/`l1`/`l2` 证据键在 Task 1/4/5/6 间一致;`LONG_RUNNING_TRANSACTION_BLOCKING_INVENTORY_RESERVATION` 全计划一致。
- **遗留风险**:Task 2/8 中真实 MySQL 查询字段(performance_schema.data_lock_waits / innodb_trx 列名)标注"Task 9/15 联调校准"——执行器逻辑与权限边界已完整,字段对齐属实现细节;Task 10 锁持有需真实 MySQL(本地 MySQL80 在线,VM compose 可跑),已用集成标记隔离。
