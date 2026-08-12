# V1.5 证据与决策链回放(Replay)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于调查时写入的不可变快照,提供只读、零副作用、前端本地播放的证据与决策链回放。

**Architecture:** 后端在 Agent 节点执行时写入 `incident_replay_step`(纯追加、两段式 phase、按 (logical_step_id, attempt_no) 组装 Attempt);Replay Projector 把阶段记录聚合为 UI 步骤(stepIndex + displayDurationMs);只读 Replay API 按 Run 限定返回 Manifest 与步骤;前端回放页以 position 语义本地播放(单次 setTimeout、状态机、控制条),不重算任何 Policy。

**Tech Stack:** Python/FastAPI/SQLAlchemy(pymysql)、LangGraph(已有)、Vue3+TS+Vite+Element Plus(已有)、pytest/vitest(已有)。

## Global Constraints

- 版本常量(policy_bundle 等)在 Agent Run 启动时冻结为 expected_*;Step 记录实际使用版本;Run 恢复前校验,不一致 → `needs_human/version_mismatch` 或新 Run。
- `incident_replay_step` 纯追加,禁止 UPDATE/DELETE;两段式 = 同 `logical_step_id` 多条 phase 记录;每个 Attempt 最多一条 started + 一条终态。
- `sequence_no` 原子分配(`agent_run.next_replay_sequence` 自增)与插入必须在同一数据库事务。
- 回放 API 全部只读:不触发状态机、不调 LLM/MCP、不执行审批处置、不重算 Policy;返回后端脱敏数据。
- source_reference 只存引用 + 冻结摘要(capturedSummary),不塞完整响应/原始 SQL/未脱敏输出。
- snapshot_hash = SHA-256(Canonical JSON),只证明一致性,不声称防篡改。
- 假设状态用现有领域枚举 proposed/confirmed;根因确认(confirmed_hypothesis_id)独立展示;不引入 supported/refuted 假设状态。
- 前端播放位置语义:position=0 显示 steps[0].stateBefore;position=N 显示 steps[N-1].stateAfter;URL `?runId=&position=`。
- 前端进回放页必须关闭 Incident SSE 与详情页轮询;播放期间业务 API 只允许 Replay GET。

---

### Task 1: agent_run 扩展字段 + 版本常量 + 序号分配

**Files:**
- Modify: `ai-service/app/db/models.py`(AgentRun 加字段)
- Modify: `ai-service/scripts/sql/04-control-schema.sql`(agent_run 加列,幂等)
- Modify: `ai-service/app/agent/policies.py`(POLICY_BUNDLE_VERSION 常量)
- Create: `ai-service/app/replay/versions.py`
- Create: `ai-service/app/repositories/run_repo.py`(allocate_replay_sequence)
- Test: `ai-service/tests/test_run_repo.py`

**Interfaces:**
- Consumes: `app.db.engine.get_control_engine()`(已有)。
- Produces:
  - `AgentRun.next_replay_sequence: int`(默认 0)、`AgentRun.expected_policy_bundle_version: str | None`、`AgentRun.policy_bundle_version: str | None`
  - `app.replay.versions.POLICY_BUNDLE_VERSION = "1.0"`、`REPLAY_SCHEMA_VERSION = "1.0"`、`PLAYBACK_POLICY_VERSION = "1"`(tool_contract 用既有 `MCP_TOOL_CONTRACT_VERSION`,normalizer 用既有 `NORMALIZER_VERSION`)
  - `run_repo.allocate_replay_sequence(agent_run_id: int) -> int`(原子自增,与插入同事务见 Task 4)
  - `run_repo.freeze_run_versions(agent_run_id: int, policy_bundle_version: str) -> None`(Run 启动时冻结 expected)

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_run_repo.py`:

```python
import pytest
from app.repositories import run_repo
from app.db.engine import get_control_engine
from app.db.models import AgentRun
from sqlalchemy.orm import Session


@pytest.fixture()
def run_id() -> int:
    with Session(get_control_engine()) as s:
        r = AgentRun(incident_id=999001, thread_id=f"t-replay-{__import__('uuid').uuid4().hex[:8]}",
                     status="created")
        s.add(r); s.commit(); s.refresh(r)
        return r.id


def test_allocate_sequence_atomic_and_monotonic(run_id):
    a = run_repo.allocate_replay_sequence(run_id)
    b = run_repo.allocate_replay_sequence(run_id)
    assert b == a + 1  # 单调递增


def test_freeze_versions(run_id):
    run_repo.freeze_run_versions(run_id, "1.0")
    from sqlalchemy import select
    with Session(get_control_engine()) as s:
        r = s.get(AgentRun, run_id)
        assert r.expected_policy_bundle_version == "1.0"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && uv run pytest tests/test_run_repo.py -q`
Expected: FAIL(allocate_replay_sequence 不存在)

- [ ] **Step 3: models.py 加字段**

`AgentRun` 追加:

```python
    next_replay_sequence: Mapped[int] = mapped_column(Integer, default=0)
    expected_policy_bundle_version: Mapped[Optional[str]] = mapped_column(String(32))
    policy_bundle_version: Mapped[Optional[str]] = mapped_column(String(32))
```

- [ ] **Step 4: DDL 追加(04-control-schema.sql,幂等)**

```sql
-- V1.5 回放:agent_run 扩展
SET @c := (SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='tracemind_control' AND TABLE_NAME='agent_run' AND COLUMN_NAME='next_replay_sequence');
SET @sql := IF(@c=0, 'ALTER TABLE agent_run ADD COLUMN next_replay_sequence INT NOT NULL DEFAULT 0',
               'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
SET @c := (SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='tracemind_control' AND TABLE_NAME='agent_run' AND COLUMN_NAME='expected_policy_bundle_version');
SET @sql := IF(@c=0, 'ALTER TABLE agent_run ADD COLUMN expected_policy_bundle_version VARCHAR(32) NULL',
               'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
SET @c := (SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='tracemind_control' AND TABLE_NAME='agent_run' AND COLUMN_NAME='policy_bundle_version');
SET @sql := IF(@c=0, 'ALTER TABLE agent_run ADD COLUMN policy_bundle_version VARCHAR(32) NULL',
               'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
```

- [ ] **Step 5: versions.py**

```python
"""V1.5 回放版本常量(Run 级冻结)。"""
POLICY_BUNDLE_VERSION = "1.0"
REPLAY_SCHEMA_VERSION = "1.0"
PLAYBACK_POLICY_VERSION = "1"
```

- [ ] **Step 6: run_repo.py**

```python
"""agent_run 的序号分配与版本冻结。"""
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun


def allocate_replay_sequence(agent_run_id: int) -> int:
    """原子分配 replay sequence_no(自增,与插入同事务由调用方保证)。"""
    with Session(get_control_engine()) as s:
        r = s.get(AgentRun, agent_run_id)
        if r is None:
            raise ValueError("agent_run not found")
        r.next_replay_sequence += 1
        s.commit()
        return r.next_replay_sequence


def freeze_run_versions(agent_run_id: int, policy_bundle_version: str) -> None:
    with Session(get_control_engine()) as s:
        s.execute(update(AgentRun).where(AgentRun.id == agent_run_id).values(
            expected_policy_bundle_version=policy_bundle_version))
        s.commit()
```

- [ ] **Step 7: 运行确认通过**

Run: `cd ai-service && uv run pytest tests/test_run_repo.py -q`
Expected: 2 passed(本地 MySQL 在线)

- [ ] **Step 8: 提交**

```bash
git add ai-service/app/db/models.py ai-service/scripts/sql/04-control-schema.sql ai-service/app/agent/policies.py ai-service/app/replay/versions.py ai-service/app/repositories/run_repo.py ai-service/tests/test_run_repo.py
git commit -m "feat(replay): agent_run 扩展(next_replay_sequence/版本冻结)+ 原子序号分配"
```

---

### Task 2: incident_replay_step 表 + 模型

**Files:**
- Modify: `ai-service/scripts/sql/04-control-schema.sql`(建表)
- Modify: `ai-service/app/db/models.py`(IncidentReplayStep)
- Test: `ai-service/tests/test_replay_models.py`

**Interfaces:**
- Consumes: Task 1 的版本常量。
- Produces: `IncidentReplayStep` ORM 模型(表结构见 spec §3.1,含 `UNIQUE(agent_run_id, logical_step_id, attempt_no, phase)`、`UNIQUE(agent_run_id, sequence_no)`、`CHECK(phase IN ('started','completed','failed'))`)。

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_replay_models.py`:

```python
from sqlalchemy import inspect
from app.db.engine import get_control_engine
from app.db.models import IncidentReplayStep
from sqlalchemy.orm import Session


def test_replay_step_table_created():
    insp = inspect(get_control_engine())
    assert "incident_replay_step" in insp.get_table_names()


def test_replay_step_insert_and_constraints():
    with Session(get_control_engine()) as s:
        step = IncidentReplayStep(
            incident_id=999002, agent_run_id=999002, logical_step_id="ls-1",
            phase="started", step_type="DIAGNOSIS_EVALUATED", sequence_no=1,
            replay_schema_version="1.0", policy_bundle_version="1.0",
        )
        s.add(step); s.commit()
        s.refresh(step)
        assert step.id is not None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && uv run pytest tests/test_replay_models.py -q`
Expected: FAIL(表不存在)

- [ ] **Step 3: DDL 建表**

```sql
CREATE TABLE IF NOT EXISTS incident_replay_step (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    incident_id BIGINT NOT NULL,
    agent_run_id BIGINT NOT NULL,
    logical_step_id VARCHAR(64) NOT NULL,
    phase VARCHAR(16) NOT NULL,
    round_no INT NULL,
    attempt_no INT NOT NULL DEFAULT 1,
    step_type VARCHAR(40) NOT NULL,
    step_title VARCHAR(128) NULL,
    step_outcome VARCHAR(32) NULL,
    sequence_no INT NOT NULL,
    state_before_json JSON NULL,
    state_after_json JSON NULL,
    decision_json JSON NULL,
    operation_json JSON NULL,
    source_references_json JSON NULL,
    actual_duration_ms INT NULL,
    replay_schema_version VARCHAR(16) NOT NULL,
    policy_bundle_version VARCHAR(32) NULL,
    prompt_version VARCHAR(64) NULL,
    tool_contract_version VARCHAR(32) NULL,
    normalization_rule_version VARCHAR(32) NULL,
    snapshot_hash VARCHAR(64) NULL,
    payload_size_bytes INT NULL,
    occurred_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uk_seq (agent_run_id, sequence_no),
    UNIQUE KEY uk_attempt_phase (agent_run_id, logical_step_id, attempt_no, phase),
    CONSTRAINT chk_phase CHECK (phase IN ('started','completed','failed')),
    INDEX idx_run (agent_run_id, sequence_no),
    INDEX idx_incident (incident_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 4: models.py 加模型**

```python
class IncidentReplayStep(Base):
    __tablename__ = "incident_replay_step"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    agent_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    logical_step_id: Mapped[str] = mapped_column(String(64), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    round_no: Mapped[Optional[int]] = mapped_column(Integer)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    step_type: Mapped[str] = mapped_column(String(40), nullable=False)
    step_title: Mapped[Optional[str]] = mapped_column(String(128))
    step_outcome: Mapped[Optional[str]] = mapped_column(String(32))
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    state_before_json: Mapped[Optional[dict]] = mapped_column(JSON)
    state_after_json: Mapped[Optional[dict]] = mapped_column(JSON)
    decision_json: Mapped[Optional[dict]] = mapped_column(JSON)
    operation_json: Mapped[Optional[dict]] = mapped_column(JSON)
    source_references_json: Mapped[Optional[dict]] = mapped_column(JSON)
    actual_duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    replay_schema_version: Mapped[str] = mapped_column(String(16))
    policy_bundle_version: Mapped[Optional[str]] = mapped_column(String(32))
    prompt_version: Mapped[Optional[str]] = mapped_column(String(64))
    tool_contract_version: Mapped[Optional[str]] = mapped_column(String(32))
    normalization_rule_version: Mapped[Optional[str]] = mapped_column(String(32))
    snapshot_hash: Mapped[Optional[str]] = mapped_column(String(64))
    payload_size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
```

- [ ] **Step 5: 本地执行 DDL + 运行确认通过**

Run: `mysql -uroot -proot tracemind_control < scripts/sql/04-control-schema.sql`(或执行建表段);`cd ai-service && uv run pytest tests/test_replay_models.py -q`
Expected: 2 passed

- [ ] **Step 6: 提交**

```bash
git add ai-service/scripts/sql/04-control-schema.sql ai-service/app/db/models.py ai-service/tests/test_replay_models.py
git commit -m "feat(replay): incident_replay_step 表 + ORM(纯追加/两段式约束)"
```

---

### Task 3: ReplaySnapshotFactory(状态快照)

**Files:**
- Create: `ai-service/app/replay/snapshot.py`
- Test: `ai-service/tests/test_replay_snapshot.py`

**Interfaces:**
- Consumes: `app.agent.state.IncidentState`(dict),`app.agent.policies`(双 Policy 状态)、`app.agent.facts`(Facts)。
- Produces:
  - `ReplayStateSnapshot`(dataclass):`hypotheses / facts / diagnostic_policies / exclusion_conditions / confirmed_root_cause / incident_status / pending_approval / recovery_status`
  - `ReplaySnapshotFactory.snapshot(state: dict) -> dict`(返回可 JSON 序列化的规范 dict)
  - `canonical_json(obj: dict) -> str`(固定键序、ensure_ascii=False)
  - `snapshot_hash(obj: dict) -> str`(SHA-256(canonical_json))

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_replay_snapshot.py`:

```python
import json
from app.replay.snapshot import ReplaySnapshotFactory, canonical_json, snapshot_hash


def _state(**over):
    base = {
        "incident_id": 1, "run_id": 2, "status": "investigating",
        "hypotheses": [{"id": "h1", "description": "缺索引", "status": "proposed"}],
        "evidence": [{"id": "E1", "key": "e1", "passed": True, "content": {"p95Ms": 117}}],
        "evidence_gate": {"E1": True},
        "facts": {"F_INDEX_MISSING": True, "F_ENDPOINT_DEGRADED": True},
        "policy": {"scn001": "supported", "scn002": "unknown"},
        "root_cause_code": None,
        "confirmed_hypothesis_id": None,
        "termination_reason": None,
        "lock_evidence_refresh_count": 0,
        "tool_call_count": 3, "decision_attempt_count": 2,
        "_internal": {"secret": "不应出现"},
    }
    base.update(over)
    return base


def test_snapshot_whitelist_filters_internal_fields():
    snap = ReplaySnapshotFactory().snapshot(_state())
    assert "_internal" not in snap
    assert "tool_call_count" not in snap  # 白名单外
    assert snap["facts"]["F_INDEX_MISSING"] is True


def test_snapshot_is_deep_copy():
    state = _state()
    snap = ReplaySnapshotFactory().snapshot(state)
    state["facts"]["F_INDEX_MISSING"] = False  # 改原 state
    assert snap["facts"]["F_INDEX_MISSING"] is True  # 快照不受影响


def test_canonical_json_and_hash_stable():
    a = {"b": 2, "a": [1, {"d": 4, "c": 3}]}
    b = {"a": [1, {"c": 3, "d": 4}], "b": 2}
    assert canonical_json(a) == canonical_json(b)
    assert snapshot_hash(a) == snapshot_hash(b)
    assert len(snapshot_hash(a)) == 64
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && uv run pytest tests/test_replay_snapshot.py -q`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: snapshot.py**

```python
"""ReplaySnapshotFactory:IncidentState → 规范脱敏快照(白名单/深拷贝/确定性排序/Canonical JSON)。"""
import copy
import hashlib
import json
from dataclasses import dataclass, field, asdict

# 白名单:只保留回放展示需要的字段
_SNAPSHOT_WHITELIST = ("hypotheses", "facts", "policy", "evidence_gate",
                       "root_cause_code", "confirmed_hypothesis_id", "status",
                       "termination_reason", "severity", "service_ref")


@dataclass
class ReplayStateSnapshot:
    hypotheses: list = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    diagnostic_policies: dict = field(default_factory=dict)
    exclusion_conditions: dict = field(default_factory=dict)
    confirmed_root_cause: str | None = None
    incident_status: str | None = None
    pending_approval: dict | None = None
    recovery_status: dict | None = None


def _deterministic_sort(obj):
    """递归确定性排序:dict 按键,list 元素排序(可排序时)。"""
    if isinstance(obj, dict):
        return {k: _deterministic_sort(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        items = [_deterministic_sort(v) for v in obj]
        try:
            return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
        except TypeError:
            return items
    return obj


def canonical_json(obj: dict) -> str:
    return json.dumps(_deterministic_sort(obj), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def snapshot_hash(obj: dict) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


class ReplaySnapshotFactory:
    def snapshot(self, state: dict) -> dict:
        src = copy.deepcopy({k: state.get(k) for k in _SNAPSHOT_WHITELIST if k in state})
        # hypotheses 按 id 排序;facts 按 code 排序;policy 按 code 排序
        hyps = sorted(src.get("hypotheses") or [], key=lambda h: str(h.get("id")))
        facts = dict(sorted((src.get("facts") or {}).items()))
        policies = dict(sorted((src.get("policy") or {}).items()))
        out = {
            "hypotheses": hyps,
            "facts": facts,
            "diagnostic_policies": policies,
            "exclusion_conditions": _exclusion_conditions(facts),
            "confirmed_root_cause": src.get("root_cause_code") or src.get("confirmed_hypothesis_id"),
            "incident_status": src.get("status"),
            "pending_approval": None,
            "recovery_status": None,
        }
        return out


def _exclusion_conditions(facts: dict) -> dict:
    """安全排他条件(Facts 推导):与根因正向证据分区展示。"""
    return {
        "X_INDEX_NORMAL": facts.get("F_INDEX_MISSING") is False,
        "X_NO_TARGET_LOCK_WAIT": facts.get("F_TARGET_LOCK_WAIT") is False,
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && uv run pytest tests/test_replay_snapshot.py -q`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/replay/snapshot.py ai-service/tests/test_replay_snapshot.py
git commit -m "feat(replay): ReplaySnapshotFactory — 白名单/深拷贝/确定性排序/Canonical JSON/SHA-256"
```

---

### Task 4: ReplayWriter(两段式写入 + 幂等)

**Files:**
- Create: `ai-service/app/replay/writer.py`
- Test: `ai-service/tests/test_replay_writer.py`

**Interfaces:**
- Consumes: `run_repo.allocate_replay_sequence`(Task 1)、`IncidentReplayStep`(Task 2)、`snapshot_hash`(Task 3)。
- Produces:
  - `ReplayWriter(incident_id: int, agent_run_id: int)`
  - `writer.write(step_type, phase, *, logical_step_id, attempt_no=1, step_title=None, step_outcome=None, round_no=None, state_before=None, state_after=None, decision=None, operation=None, source_refs=None, actual_duration_ms=None) -> IncidentReplayStep`
    - 内部:`sequence_no = run_repo.allocate_replay_sequence(agent_run_id)`(与插入在同一事务由 writer 内保证:先 begin 事务再分配+插入——见 Step 3 说明)
    - `step_type/phase` 枚举校验;`logical_step_id` 空则自动生成 `step_{uuid4().hex[:12]}`
  - `writer.existing_logical_id(step_type: str, business_key: str) -> str | None`(幂等复用:审批重试/客户端重复提交返回原 logical_step_id)
  - `writer.complete(step_type, logical_step_id, *, state_after=None, outcome=None, operation=None, source_refs=None, actual_duration_ms=None) -> IncidentReplayStep`

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_replay_writer.py`:

```python
import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.db.engine import get_control_engine
from app.db.models import IncidentReplayStep
from app.replay.writer import ReplayWriter


@pytest.fixture()
def run_id() -> int:
    from app.db.models import AgentRun
    with Session(get_control_engine()) as s:
        r = AgentRun(incident_id=999003, thread_id=f"t-w-{__import__('uuid').uuid4().hex[:8]}",
                     status="created")
        s.add(r); s.commit(); s.refresh(r)
        return r.id


def test_write_started_then_completed_two_rows_same_logical_id(run_id):
    w = ReplayWriter(999003, run_id)
    w.write("EVIDENCE_COLLECTION", "started", logical_step_id="ls-e1",
            state_before={"facts": {}})
    w.complete("EVIDENCE_COLLECTION", "ls-e1", state_after={"facts": {"F_INDEX_MISSING": True}},
               outcome="succeeded")
    with Session(get_control_engine()) as s:
        rows = s.scalars(select(IncidentReplayStep).where(
            IncidentReplayStep.agent_run_id == run_id)).all()
        assert len(rows) == 2
        phases = sorted(r.phase for r in rows)
        assert phases == ["completed", "started"]
        assert {r.sequence_no for r in rows} == {1, 2}
        assert rows[0].logical_step_id == rows[1].logical_step_id == "ls-e1"


def test_write_failed_phase_constraint_rejects_unknown_phase(run_id):
    w = ReplayWriter(999003, run_id)
    with pytest.raises(Exception):
        w.write("DIAGNOSIS_EVALUATED", "weird_phase", logical_step_id="ls-bad")


def test_approval_idempotent_reuses_logical_id(run_id):
    w = ReplayWriter(999003, run_id)
    lid = w.existing_logical_id("APPROVAL_DECIDED", "approval:42")
    assert lid is None
    w.write("APPROVAL_DECIDED", "started", logical_step_id="ls-app",
            source_refs={"approval_id": 42})
    lid2 = w.existing_logical_id("APPROVAL_DECIDED", "approval:42")
    assert lid2 == "ls-app"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && uv run pytest tests/test_replay_writer.py -q`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: writer.py(序号+插入同事务)**

> 序号分配与插入同事务:writer 内 `allocate_replay_sequence` 与 INSERT 使用**同一个 Session** 提交;因此 Task 1 的 `allocate_replay_sequence(agent_run_id, session=None)` 需支持传入会话。实现时把分配函数签名改为 `allocate_replay_sequence(agent_run_id, session)`(复用传入 Session,不自行 open),Task 1 测试同步更新。

```python
"""ReplayWriter:两段式写入(started → completed/failed),纯追加,logical_step_id 幂等。"""
import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import IncidentReplayStep
from app.replay.snapshot import snapshot_hash
from app.replay.versions import REPLAY_SCHEMA_VERSION

PHASES = ("started", "completed", "failed")

# step_type 业务枚举(与 spec §3.4 一致;不用 Python 函数名)
STEP_TYPES = (
    "INCIDENT_INGESTED", "HYPOTHESES_GENERATED", "EVIDENCE_COLLECTION",
    "DIAGNOSIS_EVALUATED", "FIX_PROPOSED", "APPROVAL_REQUESTED",
    "APPROVAL_DECIDED", "ACTION_REVALIDATED", "FIX_EXECUTED",
    "RECOVERY_VERIFIED", "REPORT_GENERATED", "RUN_TERMINATED",
)


class ReplayWriter:
    def __init__(self, incident_id: int, agent_run_id: int):
        self.incident_id = incident_id
        self.agent_run_id = agent_run_id

    def _allocate(self, session: Session) -> int:
        from app.repositories import run_repo
        return run_repo.allocate_replay_sequence(self.agent_run_id, session)

    def existing_logical_id(self, step_type: str, business_key: str) -> str | None:
        with Session(get_control_engine()) as s:
            row = s.scalars(select(IncidentReplayStep).where(
                IncidentReplayStep.agent_run_id == self.agent_run_id,
                IncidentReplayStep.step_type == step_type,
                IncidentReplayStep.source_references_json.isnot(None)).limit(50)).all()
        for r in row:
            refs = r.source_references_json or {}
            if refs.get("businessKey") == business_key:
                return r.logical_step_id
        return None

    def write(self, step_type: str, phase: str, *, logical_step_id: str | None = None,
              attempt_no: int = 1, step_title: str | None = None,
              step_outcome: str | None = None, round_no: int | None = None,
              state_before: dict | None = None, state_after: dict | None = None,
              decision: dict | None = None, operation: dict | None = None,
              source_refs: dict | None = None,
              actual_duration_ms: int | None = None) -> IncidentReplayStep:
        if step_type not in STEP_TYPES:
            raise ValueError(f"unknown step_type: {step_type}")
        if phase not in PHASES:
            raise ValueError(f"unknown phase: {phase}")
        lid = logical_step_id or f"step_{uuid.uuid4().hex[:12]}"
        with Session(get_control_engine()) as s:
            seq = self._allocate(s)  # 与插入同一事务
            payload = {"hypotheses": state_before or {}, "facts": state_before or {}}
            size = len(str(source_refs or {})) + len(str(decision or {}))
            step = IncidentReplayStep(
                incident_id=self.incident_id, agent_run_id=self.agent_run_id,
                logical_step_id=lid, phase=phase, attempt_no=attempt_no,
                step_type=step_type, step_title=step_title, step_outcome=step_outcome,
                round_no=round_no, sequence_no=seq,
                state_before_json=state_before, state_after_json=state_after,
                decision_json=decision, operation_json=operation,
                source_references_json=source_refs,
                actual_duration_ms=actual_duration_ms,
                replay_schema_version=REPLAY_SCHEMA_VERSION,
                snapshot_hash=snapshot_hash(state_before or {}),
                payload_size_bytes=size,
            )
            s.add(step); s.commit(); s.refresh(step)
            return step

    def complete(self, step_type: str, logical_step_id: str, *,
                 state_after: dict | None = None, outcome: str | None = None,
                 operation: dict | None = None, source_refs: dict | None = None,
                 actual_duration_ms: int | None = None) -> IncidentReplayStep:
        return self.write(step_type, "completed", logical_step_id=logical_step_id,
                          step_outcome=outcome, state_after=state_after,
                          operation=operation, source_refs=source_refs,
                          actual_duration_ms=actual_duration_ms)

    def fail(self, step_type: str, logical_step_id: str, *,
             outcome: str | None = None, operation: dict | None = None,
             actual_duration_ms: int | None = None) -> IncidentReplayStep:
        return self.write(step_type, "failed", logical_step_id=logical_step_id,
                          step_outcome=outcome, operation=operation,
                          actual_duration_ms=actual_duration_ms)
```

- [ ] **Step 4: 更新 Task 1 的 run_repo.allocate_replay_sequence 支持传入 Session**

把 `allocate_replay_sequence(agent_run_id)` 改为 `allocate_replay_sequence(agent_run_id, session)`:在传入 Session 内执行 `r.next_replay_sequence += 1; session.commit()`,返回新值。Task 1 测试改用 `with Session(...)` 传入。

- [ ] **Step 5: 运行确认通过**

Run: `cd ai-service && uv run pytest tests/test_replay_writer.py tests/test_run_repo.py -q`
Expected: 5 passed(序号单调、两段式两行、phase 校验、幂等复用)

- [ ] **Step 6: 提交**

```bash
git add ai-service/app/replay/writer.py ai-service/app/repositories/run_repo.py ai-service/tests/test_replay_writer.py ai-service/tests/test_run_repo.py
git commit -m "feat(replay): ReplayWriter 两段式写入(序号+插入同事务,logical_step_id 幂等)"
```

### Task 5: 节点接入快照采集(ingest/hypothesize/diagnose/propose_fix/report)

**Files:**
- Modify: `ai-service/app/agent/nodes.py`(各节点函数包裹快照)
- Modify: `ai-service/app/agent/graph.py`(RUN_TERMINATED + Run 启动冻结版本)
- Test: `ai-service/tests/test_replay_node_integration.py`

**Interfaces:**
- Consumes: `ReplayWriter`(Task 4)、`ReplaySnapshotFactory`(Task 3)。
- Produces: 各节点执行时写入对应 step_type 的两段式记录;graph 结束时写 `RUN_TERMINATED`。

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_replay_node_integration.py`(调用真实节点函数,验证快照写入):

```python
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.engine import get_control_engine
from app.db.models import IncidentReplayStep, AgentRun
from app.agent import nodes
from app.replay.writer import ReplayWriter


@pytest.fixture()
def run_id() -> int:
    with Session(get_control_engine()) as s:
        r = AgentRun(incident_id=999004, thread_id=f"t-ni-{__import__('uuid').uuid4().hex[:8]}",
                     status="created")
        s.add(r); s.commit(); s.refresh(r)
        return r.id


def _base_state(run_id):
    return {"incident_id": 999004, "run_id": run_id, "service_ref": "inventory-service",
            "severity": "high", "status": "investigating",
            "hypotheses": [{"id": "h1", "description": "缺索引", "status": "proposed"}],
            "evidence": [], "evidence_gate": {}, "facts": {}, "policy": {},
            "root_cause_code": None, "confirmed_hypothesis_id": None,
            "termination_reason": None, "max_investigation_rounds": 5, "max_tool_calls": 25}


def test_ingest_writes_step(run_id, monkeypatch):
    monkeypatch.setattr("app.agent.nodes.replay_writer_for", lambda iid, rid: ReplayWriter(999004, run_id))
    out = nodes.ingest(_base_state(run_id))
    with Session(get_control_engine()) as s:
        rows = s.scalars(select(IncidentReplayStep).where(
            IncidentReplayStep.agent_run_id == run_id,
            IncidentReplayStep.step_type == "INCIDENT_INGESTED")).all()
        assert len(rows) == 2  # started + completed
        assert any(r.phase == "started" for r in rows)
        assert any(r.phase == "completed" for r in rows)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && uv run pytest tests/test_replay_node_integration.py -q`
Expected: FAIL(replay_writer_for 不存在)

- [ ] **Step 3: nodes.py 增加 replay 接入辅助**

在 nodes.py 顶部:

```python
from app.replay.writer import ReplayWriter, STEP_TYPES
from app.replay.snapshot import ReplaySnapshotFactory

_snapshot_factory = ReplaySnapshotFactory()
_writer_registry: dict[int, ReplayWriter] = {}  # key=(incident_id, run_id) → writer(测试可 monkeypatch)


def replay_writer_for(incident_id: int, run_id: int) -> ReplayWriter:
    return _writer_registry.setdefault((incident_id, run_id), ReplayWriter(incident_id, run_id))


def _snap(state: dict) -> dict:
    return _snapshot_factory.snapshot(state)
```

- [ ] **Step 4: ingest 包裹**

```python
def ingest(state: IncidentState) -> dict:
    run_id = state.get("run_id")
    writer = replay_writer_for(state["incident_id"], run_id)
    lid = f"ls-ingest-{run_id}"
    before = _snap(state)
    writer.write("INCIDENT_INGESTED", "started", logical_step_id=lid,
                 state_before=before, source_refs={"businessKey": f"ingest:{run_id}"})
    state["status"] = "investigating"
    _emit_status(state)
    after = _snap({**state, "status": "investigating"})
    writer.complete("INCIDENT_INGESTED", lid, state_after=after, outcome="succeeded")
    return state
```

> 其余节点(hypothesize/diagnose/propose_fix/report)按同样模式包裹,step_type 分别为 `HYPOTHESES_GENERATED / DIAGNOSIS_EVALUATED / FIX_PROPOSED / REPORT_GENERATED`;state_after 用 `{**state, **out}` 近似合并(节点返回增量 dict)。collect_evidence 与 human_approval 单独处理(见 Task 6/7)。

- [ ] **Step 5: graph.py 收尾(冻结版本 + RUN_TERMINATED)**

在 `build_graph` 的终态边处理处(或 runner 收尾处)追加:

```python
from app.replay.versions import POLICY_BUNDLE_VERSION
from app.replay.writer import ReplayWriter
from app.repositories import run_repo


def _finalize_run(incident_id: int, run_id: int, status: str, termination_reason: str | None) -> None:
    run_repo.freeze_run_versions(run_id, POLICY_BUNDLE_VERSION)
    writer = ReplayWriter(incident_id, run_id)
    lid = f"ls-term-{run_id}"
    writer.write("RUN_TERMINATED", "completed", logical_step_id=lid,
                 step_outcome=_outcome_for(status, termination_reason),
                 source_refs={"businessKey": f"terminated:{run_id}"},
                 decision={"runStatus": status, "terminationReason": termination_reason})


def _outcome_for(status: str, reason: str | None) -> str:
    if status == "recovered":
        return "succeeded"
    if status == "rejected":
        return "rejected"
    if status == "needs_human":
        return "needs_human"
    return "failed"
```

- [ ] **Step 6: 运行确认通过**

Run: `cd ai-service && uv run pytest tests/test_replay_node_integration.py -q`
Expected: PASS

- [ ] **Step 7: 全量回归 + 提交**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全绿(节点包裹不改变业务行为;测试用 monkeypatch 隔离 writer)

```bash
git add ai-service/app/agent/nodes.py ai-service/app/agent/graph.py ai-service/tests/test_replay_node_integration.py
git commit -m "feat(replay): 节点接入快照采集(ingest/hypothesize/diagnose/propose_fix/report)+ RUN_TERMINATED + 版本冻结"
```

---

### Task 6: collect_evidence 每轮快照 + EVIDENCE_COLLECTION

**Files:**
- Modify: `ai-service/app/agent/nodes.py`(collect_evidence)
- Test: `ai-service/tests/test_replay_collect_evidence.py`

**Interfaces:**
- Consumes: Task 5 的 `replay_writer_for` / `_snap`。
- Produces: collect_evidence **每轮**写一条 `EVIDENCE_COLLECTION` 两段式记录(round_no 标记轮次,decision 含 eligible/selected/validation,operation 含工具执行,source_refs 含 tool_call_id/evidence_ids)。

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_replay_collect_evidence.py`:

```python
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.engine import get_control_engine
from app.db.models import IncidentReplayStep, AgentRun
from app.agent import nodes
from app.replay.writer import ReplayWriter


@pytest.fixture()
def run_id() -> int:
    with Session(get_control_engine()) as s:
        r = AgentRun(incident_id=999005, thread_id=f"t-ce-{__import__('uuid').uuid4().hex[:8]}",
                     status="created")
        s.add(r); s.commit(); s.refresh(r)
        return r.id


class StubLLM:
    def __init__(self, responses): self.responses = list(responses)
    def __call__(self, state, eligible):
        return (self.responses.pop(0) if self.responses else []) or ([{"id": "h1"}] if False else [])


def _stub_tools(run_id):
    def fake(state, name, args):
        if name == "get_service_metrics":
            return {"ok": True, "evidence": [{"id": "E1", "key": "e1",
                    "source": "get_service_metrics", "content": {"p95Ms": 117,
                    "sourceBackend": "prometheus"}, "passed": True}]}
        return {"ok": False, "evidence": []}
    return fake


def test_collect_evidence_writes_per_round(run_id, monkeypatch):
    monkeypatch.setattr("app.agent.nodes.replay_writer_for", lambda iid, rid: ReplayWriter(999005, run_id))
    state = {"incident_id": 999005, "run_id": run_id, "service_ref": "inventory-service",
             "severity": "high", "status": "investigating", "hypotheses": [],
             "evidence": [], "evidence_gate": {}, "facts": {}, "policy": {},
             "max_investigation_rounds": 2, "max_tool_calls": 25,
             "decision_attempt_count": 0, "tool_call_count": 0,
             "consecutive_no_progress_count": 0, "consecutive_invalid_count": 0}
    nodes.collect_evidence(state, llm=StubLLM([[]]), tools=_stub_tools(run_id))
    with Session(get_control_engine()) as s:
        rows = s.scalars(select(IncidentReplayStep).where(
            IncidentReplayStep.agent_run_id == run_id,
            IncidentReplayStep.step_type == "EVIDENCE_COLLECTION")).all()
        assert len(rows) >= 2  # 至少 started + completed
        rounds = {r.round_no for r in rows if r.round_no is not None}
        assert len(rounds) >= 1
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && uv run pytest tests/test_replay_collect_evidence.py -q`
Expected: FAIL(collect_evidence 未写 EVIDENCE_COLLECTION)

- [ ] **Step 3: collect_evidence 每轮包裹**

在 `collect_evidence` 循环内,决策后、执行前捕获 before,执行并评估证据后捕获 after:

```python
    # 轮次快照:EVIDENCE_COLLECTION
    run_id = state.get("run_id")
    writer = replay_writer_for(state["incident_id"], run_id)
    lid = f"ls-ev-{run_id}-r{decision}"
    round_before = _snap(state)
    writer.write("EVIDENCE_COLLECTION", "started", logical_step_id=lid,
                 round_no=decision, state_before=round_before,
                 decision={"eligibleTools": sorted(eligible),
                           "selectedTool": name,
                           "decisionSummary": decision_summary,
                           "validationResult": validation_result},
                 source_refs={"businessKey": f"evidence:{run_id}:{decision}"})
    # ...(现有:validate/resolve/guard/execute/评估证据)...
    # 在执行成功分支(evidence 产生)与 noop 分支末尾各补:
    round_after = _snap({**state, **out})
    writer.complete("EVIDENCE_COLLECTION", lid, round_no=decision,
                    state_after=round_after,
                    outcome="succeeded" if out.get("evidence") else "no_progress",
                    operation={"toolName": name,
                               "resolvedParameters": resolved,
                               "transport": _last_transport(state, name),
                               "resultStatus": "success" if result.get("ok") else "error"},
                    source_refs={"toolCallId": _last_tool_call_id(state, name),
                                 "evidenceIds": [e.get("id") for e in out.get("evidence") or []]})
```

> `_last_transport(state, name)` / `_last_tool_call_id(state, name)` 从 `tool_calls_record`(state 内)取该工具最近一条的 transport 与记录 id;collect_evidence 循环内保证每轮恰好一条 EVIDENCE_COLLECTION started+completed/failed。执行失败且无证据时写 failed 段(`writer.fail(...)`)而非 completed。

- [ ] **Step 4: 运行确认通过**

Run: `cd ai-service && uv run pytest tests/test_replay_collect_evidence.py -q`
Expected: PASS

- [ ] **Step 5: 全量回归 + 提交**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全绿

```bash
git add ai-service/app/agent/nodes.py ai-service/tests/test_replay_collect_evidence.py
git commit -m "feat(replay): collect_evidence 每轮 EVIDENCE_COLLECTION 快照(decision/operation/sourceRefs)"
```

---

### Task 7: 审批决定快照(外部 API,幂等)+ KILL 两段式

**Files:**
- Modify: `ai-service/app/api/approvals.py`(decision 写 APPROVAL_DECIDED)
- Modify: `ai-service/app/agent/nodes.py`(human_approval 写 APPROVAL_REQUESTED;execute_fix 写 FIX_EXECUTED 两段式)
- Test: `ai-service/tests/test_replay_approval.py`

**Interfaces:**
- Consumes: `ReplayWriter.existing_logical_id`(幂等,Task 4)。
- Produces:
  - `APPROVAL_REQUESTED`(human_approval 节点写,含 pending_approval 快照)
  - `APPROVAL_DECIDED`(外部 API 写,`businessKey=approval:{approval_id}` 幂等复用 logical_step_id,避免重复提交生成重复步骤)
  - `FIX_EXECUTED` 两段式:KILL 场景 `started` → FixExecution 原子抢占 → `completed/failed`

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_replay_approval.py`:

```python
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.engine import get_control_engine
from app.db.models import IncidentReplayStep, AgentRun, Approval, FixProposal
from app.replay.writer import ReplayWriter
from app.api import approvals as approvals_api


@pytest.fixture()
def run_id() -> int:
    with Session(get_control_engine()) as s:
        r = AgentRun(incident_id=999006, thread_id=f"t-ap-{__import__('uuid').uuid4().hex[:8]}",
                     status="created")
        s.add(r); s.commit(); s.refresh(r)
        return r.id


def test_approval_decision_writes_step_once(run_id):
    # 预置 Approval
    from app.db.models import FixDefinition
    with Session(get_control_engine()) as s:
        fd = s.scalars(select(FixDefinition).limit(1)).first()
        prop = FixProposal(incident_id=999006, fix_definition_id=fd.id,
                           parameters_json={"a": 1}, parameters_hash="h", risk_level="medium")
        s.add(prop); s.commit(); s.refresh(prop)
        ap = Approval(incident_id=999006, fix_proposal_id=prop.id, action_type="CREATE_INVENTORY_INDEX",
                      parameters_hash="h", status="pending")
        s.add(ap); s.commit(); s.refresh(ap)
        approval_id, prop_id = ap.id, prop.id

    # 模拟 API decision(通过 DecisionIn)
    w = ReplayWriter(999006, run_id)
    approvals_api.replay_writer = w  # monkeypatch 注入
    # 第一次决定
    approvals_api._record_approval_decided(999006, run_id, approval_id, "approved")
    # 重复提交(客户端重试)→ 不新增步骤
    approvals_api._record_approval_decided(999006, run_id, approval_id, "approved")
    with Session(get_control_engine()) as s:
        rows = s.scalars(select(IncidentReplayStep).where(
            IncidentReplayStep.step_type == "APPROVAL_DECIDED",
            IncidentReplayStep.agent_run_id == run_id)).all()
        assert len(rows) == 2  # 一次逻辑步骤的 started+completed,非两次
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && uv run pytest tests/test_replay_approval.py -q`
Expected: FAIL(_record_approval_decided 不存在)

- [ ] **Step 3: approvals.py 增加 APPROVAL_DECIDED 记录**

```python
from app.replay.writer import ReplayWriter
from app.replay.snapshot import ReplaySnapshotFactory

replay_writer: ReplayWriter | None = None  # 测试注入;生产从 runner 获取


def _record_approval_decided(incident_id: int, run_id: int,
                             approval_id: int, decision: str,
                             comment: str | None = None) -> None:
    writer = replay_writer or ReplayWriter(incident_id, run_id)
    business_key = f"approval:{approval_id}"
    lid = writer.existing_logical_id("APPROVAL_DECIDED", business_key) \
        or f"ls-app-{approval_id}"
    writer.write("APPROVAL_DECIDED", "started", logical_step_id=lid,
                 step_title="审批决定", state_before=None,
                 decision={"decision": decision, "comment": comment},
                 source_refs={"approval_id": approval_id,
                              "businessKey": business_key})
    writer.complete("APPROVAL_DECIDED", lid, outcome=decision,
                    decision={"decision": decision, "comment": comment},
                    source_refs={"approval_id": approval_id,
                                 "businessKey": business_key})
```

在 `decide()` 成功分支(approval_repo.update_approval 之后)调用 `_record_approval_decided(...)`。

- [ ] **Step 4: human_approval 写 APPROVAL_REQUESTED**

`human_approval` 节点(等待审批前)写入:

```python
    writer = replay_writer_for(state["incident_id"], state.get("run_id"))
    lid = f"ls-req-{state['incident_id']}"
    writer.write("APPROVAL_REQUESTED", "completed", logical_step_id=lid,
                 step_outcome="requested",
                 state_before=_snap(state),
                 source_refs={"approval_id": approval_id, "fix_proposal_id": prop_id},
                 decision={"actionType": fix["action_type"], "riskLevel": fix["risk_level"]})
```

- [ ] **Step 5: execute_fix 写 FIX_EXECUTED 两段式**

在 `execute_fix`(安全控制节点)按 KILL 流程包裹:

```python
    writer = replay_writer_for(state["incident_id"], state.get("run_id"))
    lid = f"ls-fix-{state['incident_id']}"
    writer.write("FIX_EXECUTED", "started", logical_step_id=lid,
                 state_before=_snap(state),
                 source_refs={"approval_id": approval_id, "fix_proposal_id": prop_id,
                              "businessKey": f"fix:{state['incident_id']}"})
    # ...(现有六项校验 + FixExecution 原子抢占 + 执行 KILL)...
    # 成功:
    writer.complete("FIX_EXECUTED", lid, outcome="succeeded",
                    state_after=_snap({**state, "status": "executing"}),
                    operation={"actionType": "TERMINATE_BLOCKING_SESSION",
                               "killAttempted": True,
                               "actualProcesslistId": pid},
                    source_refs={"fix_execution_id": fix_execution_id})
    # 失败(未审批/关系不一致/被禁止账号等):
    writer.fail("FIX_EXECUTED", lid, outcome=result.get("execution_result", "failed"),
                operation={"rejectionRule": rejection_rule})
```

- [ ] **Step 6: 运行确认通过 + 全量回归**

Run: `cd ai-service && uv run pytest tests/test_replay_approval.py tests/ -q`
Expected: 全绿

- [ ] **Step 7: 提交**

```bash
git add ai-service/app/api/approvals.py ai-service/app/agent/nodes.py ai-service/tests/test_replay_approval.py
git commit -m "feat(replay): 审批决定快照(幂等 businessKey)+ FIX_EXECUTED 两段式(KILL 流程)"
```

---

### Task 8: 完整性检查 + Replay Projector

**Files:**
- Create: `ai-service/app/replay/integrity.py`
- Create: `ai-service/app/replay/projector.py`
- Test: `ai-service/tests/test_replay_projector.py`

**Interfaces:**
- Consumes: `IncidentReplayStep` 记录、`agent_run.status/finished_at`、`AgentRun.expected_policy_bundle_version`。
- Produces:
  - `check_replay_status(agent_run_id: int) -> dict`(replayStatus/runStatus/runOutcome/terminationReason/缺失项列表)
  - `project_run(agent_run_id: int) -> dict`(ReplayProjector 输出:`totalSteps/keyStepIndexes/steps[]`,每步含 `stepIndex/logicalStepId/sourceSequenceNos/stepState/stepOutcome/stateBefore/stateAfter/decisionSummary/operationSummary/sourceReferenceSummary/actualDurationMs/displayDurationMs`)
  - `project_step_detail(agent_run_id: int, logical_step_id: str) -> dict`(懒加载技术详情,校验引用 Hash)

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_replay_projector.py`:

```python
from app.replay.projector import ReplayProjector
from app.replay.integrity import check_replay_status


def test_projector_groups_phases_into_steps():
    # 构造:两个 logical step,其一为 started+completed,另一为 started 无终态
    steps = [
        {"logical_step_id": "a", "phase": "started", "sequence_no": 1,
         "step_type": "EVIDENCE_COLLECTION", "step_outcome": None,
         "state_before_json": {"facts": {}}, "state_after_json": None,
         "decision_json": {"selectedTool": "get_trace"}, "operation_json": None,
         "source_references_json": {}, "actual_duration_ms": 10},
        {"logical_step_id": "a", "phase": "completed", "sequence_no": 2,
         "step_type": "EVIDENCE_COLLECTION", "step_outcome": "succeeded",
         "state_before_json": None, "state_after_json": {"facts": {"F_INDEX_MISSING": True}},
         "decision_json": None, "operation_json": {"toolName": "get_trace"},
         "source_references_json": {}, "actual_duration_ms": 5},
        {"logical_step_id": "b", "phase": "started", "sequence_no": 3,
         "step_type": "FIX_EXECUTED", "step_outcome": None,
         "state_before_json": {"facts": {}}, "state_after_json": None,
         "decision_json": {}, "operation_json": None, "source_references_json": {},
         "actual_duration_ms": None},
    ]
    out = ReplayProjector().project(steps, {"replayStatus": "partial"})
    assert out["totalSteps"] == 2
    step_a = out["steps"][0]
    assert step_a["stepIndex"] == 0 and step_a["logicalStepId"] == "a"
    assert step_a["sourceSequenceNos"] == [1, 2]
    assert step_a["stepState"] == "completed" and step_a["stepOutcome"] == "succeeded"
    assert step_a["stateAfter"]["facts"]["F_INDEX_MISSING"] is True
    step_b = out["steps"][1]
    assert step_b["stepState"] == "incomplete"
    assert "stateAfter" in step_b["missingParts"]
    assert step_b["displayDurationMs"] > 0  # 投影层计算


def test_check_replay_status_partial_when_started_without_terminal():
    status = check_replay_status._evaluate_for_test(
        phases_by_logical=[("a", {"started", "completed"}), ("b", {"started"})],
        run_terminated=True)
    assert status["replayStatus"] == "partial"
    assert "b" in status["incompleteSteps"]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && uv run pytest tests/test_replay_projector.py -q`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: integrity.py**

```python
"""回放完整性检查:complete/partial/in_progress/unsupported/unavailable + runOutcome。"""
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.db.engine import get_control_engine
from app.db.models import IncidentReplayStep, AgentRun


def check_replay_status(agent_run_id: int) -> dict:
    with Session(get_control_engine()) as s:
        run = s.get(AgentRun, agent_run_id)
        if run is None:
            return {"replayStatus": "unavailable", "runStatus": "unknown"}
        rows = s.scalars(select(IncidentReplayStep).where(
            IncidentReplayStep.agent_run_id == agent_run_id)).all()
    phases_by_logical: dict[str, set[str]] = {}
    for r in rows:
        phases_by_logical.setdefault(r.logical_step_id, set()).add(r.phase)
    return _evaluate(phases_by_logical, run.status, run.finished_at is not None)


def _evaluate(phases_by_logical: dict, run_status: str, run_terminated: bool) -> dict:
    incomplete = [lid for lid, phases in phases_by_logical.items()
                  if "started" in phases and not ({"completed", "failed"} & phases)]
    run_terminated = run_terminated or run_status in ("completed", "failed", "cancelled")
    if not run_terminated:
        return {"replayStatus": "in_progress", "runStatus": run_status,
                "incompleteSteps": []}
    if incomplete:
        return {"replayStatus": "partial", "runStatus": "terminated",
                "incompleteSteps": incomplete}
    return {"replayStatus": "complete", "runStatus": "terminated",
            "incompleteSteps": []}
```

- [ ] **Step 4: projector.py**

```python
"""ReplayProjector:阶段记录 → UI 步骤(Attempt 组装 + 时长投影 + 版本适配)。"""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.engine import get_control_engine
from app.db.models import IncidentReplayStep
from app.replay.versions import REPLAY_SCHEMA_VERSION, PLAYBACK_POLICY_VERSION

# 每 step_type 的默认展示时长(ms);实际 = 按策略投影
_DISPLAY_MS = {
    "INCIDENT_INGESTED": 1500, "HYPOTHESES_GENERATED": 2500,
    "EVIDENCE_COLLECTION": 3000, "DIAGNOSIS_EVALUATED": 2500,
    "FIX_PROPOSED": 2000, "APPROVAL_REQUESTED": 1500,
    "APPROVAL_DECIDED": 2500, "ACTION_REVALIDATED": 2000,
    "FIX_EXECUTED": 2500, "RECOVERY_VERIFIED": 3500,
    "REPORT_GENERATED": 2000, "RUN_TERMINATED": 1000,
}

# 关键节点选择:取满足条件的步骤(第一次确认根因/审批决定/最终处置/最终恢复)
_KEY_TYPES = {"diagnosis": "DIAGNOSIS_EVALUATED", "approval": "APPROVAL_DECIDED",
              "execution": "FIX_EXECUTED", "recovery": "RECOVERY_VERIFIED"}


class ReplayProjector:
    def project(self, rows: list, meta: dict | None = None) -> dict:
        """rows: IncidentReplayStep 对象列表(按 sequence_no 升序)。"""
        by_logical: dict[str, list] = {}
        for r in rows:
            by_logical.setdefault(r.logical_step_id, []).append(r)
        ordered = sorted(by_logical.items(),
                         key=lambda kv: min(x.sequence_no for x in kv[1]))
        steps = []
        for idx, (lid, recs) in enumerate(ordered):
            recs.sort(key=lambda x: x.sequence_no)
            started = next((x for x in recs if x.phase == "started"), None)
            terminal = next((x for x in recs if x.phase in ("completed", "failed")), None)
            state_before = started.state_before_json if started else None
            state_after = terminal.state_after_json if terminal else None
            step_state = ("completed" if terminal and terminal.phase == "completed"
                          else "failed" if terminal else "incomplete")
            missing = []
            if not terminal:
                missing = ["stateAfter", "operationResult"]
            elif not state_after:
                missing.append("stateAfter")
            src = (started or terminal).source_references_json or {}
            actual = sum(x.actual_duration_ms or 0 for x in recs)
            steps.append({
                "stepIndex": idx,
                "logicalStepId": lid,
                "sourceSequenceNos": [x.sequence_no for x in recs],
                "stepState": step_state,
                "stepOutcome": (terminal.step_outcome if terminal else None),
                "stepType": (terminal or started).step_type,
                "stepTitle": (terminal or started).step_title,
                "stateBefore": state_before,
                "stateAfter": state_after,
                "missingParts": missing,
                "decisionSummary": (started.decision_json or {}),
                "operationSummary": (terminal.operation_json or {}),
                "sourceReferenceSummary": src,
                "actualDurationMs": actual,
                "displayDurationMs": self._display_ms(actual,
                                                      (terminal or started).step_type),
            })
        key_indexes = {}
        for key, stype in _KEY_TYPES.items():
            idxs = [i for i, st in enumerate(steps) if st["stepType"] == stype
                    and st["stepState"] == "completed"]
            if key == "diagnosis":
                # 第一次确认根因的诊断
                idxs = [i for i, st in enumerate(steps)
                        if st["stepType"] == stype and st["stepOutcome"] == "confirmed"]
            if idxs:
                key_indexes[key] = idxs[-1] if key in ("execution", "recovery") else idxs[0]
        return {"totalSteps": len(steps), "keyStepIndexes": key_indexes,
                "steps": steps, "replaySchemaVersion": REPLAY_SCHEMA_VERSION,
                "playbackPolicyVersion": PLAYBACK_POLICY_VERSION}

    @staticmethod
    def _display_ms(actual: int, step_type: str) -> int:
        base = _DISPLAY_MS.get(step_type, 2000)
        if actual <= 0:
            return base
        # 压缩:真实 60s+ 压缩到 base*2;真实 1s 内用 base;否则线性但封顶 base*2
        return max(base // 2, min(base * 2, int(actual / 1000) * 500 + base))
```

- [ ] **Step 5: 运行确认通过**

Run: `cd ai-service && uv run pytest tests/test_replay_projector.py -q`
Expected: 2 passed

- [ ] **Step 6: 提交**

```bash
git add ai-service/app/replay/integrity.py ai-service/app/replay/projector.py ai-service/tests/test_replay_projector.py
git commit -m "feat(replay): 完整性检查 + ReplayProjector(Attempt 组装/displayDurationMs/keyStepIndexes)"
```

### Task 9: Replay API(只读,按 Run 限定)

**Files:**
- Create: `ai-service/app/api/replay.py`
- Modify: `ai-service/app/main.py`(include router)
- Test: `ai-service/tests/test_replay_api.py`

**Interfaces:**
- Consumes: `check_replay_status`(Task 8)、`ReplayProjector`(Task 8)、`IncidentReplayStep`/`AgentRun` 查询。
- Produces 路由(全部只读、后端脱敏):
  - `GET /api/incidents/{incident_id}/replay`(Incident 级 Manifest + run 列表 + defaultRunId)
  - `GET /api/incidents/{incident_id}/replay/runs/{agent_run_id}`(单 Run Manifest)
  - `GET /api/incidents/{incident_id}/replay/runs/{agent_run_id}/steps`(一次返回播放必需数据)
  - `GET /api/incidents/{incident_id}/replay/runs/{agent_run_id}/steps/{logical_step_id}`(单步技术详情)

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_replay_api.py`(用 TestClient;需要本地 MySQL 有 run 数据):

```python
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.main import app
from app.db.engine import get_control_engine
from app.db.models import AgentRun, IncidentReplayStep

client = TestClient(app)


def _make_run_with_steps() -> tuple[int, int]:
    from app.replay.writer import ReplayWriter
    with Session(get_control_engine()) as s:
        r = AgentRun(incident_id=999007, thread_id=f"t-api-{__import__('uuid').uuid4().hex[:8]}",
                     status="completed", finished_at=__import__('datetime').datetime.utcnow())
        s.add(r); s.commit(); s.refresh(r)
        run_id = r.id
    w = ReplayWriter(999007, run_id)
    w.write("INCIDENT_INGESTED", "started", logical_step_id="ls-a")
    w.complete("INCIDENT_INGESTED", "ls-a", outcome="succeeded")
    return 999007, run_id


def test_replay_manifest_and_steps():
    incident_id, run_id = _make_run_with_steps()
    r = client.get(f"/api/incidents/{incident_id}/replay")
    assert r.status_code == 200
    m = r.json()
    assert m["defaultRunId"] == run_id
    assert m["replayStatus"] in ("complete", "in_progress", "partial")

    r2 = client.get(f"/api/incidents/{incident_id}/replay/runs/{run_id}/steps")
    assert r2.status_code == 200
    body = r2.json()
    assert body["totalSteps"] >= 1
    s0 = body["steps"][0]
    assert "stateBefore" in s0 and "stateAfter" in s0
    assert "displayDurationMs" in s0 and "actualDurationMs" in s0


def test_replay_run_belongs_to_incident():
    incident_id, run_id = _make_run_with_steps()
    # 用另一个 incident_id 访问该 run → 404
    r = client.get(f"/api/incidents/999999/replay/runs/{run_id}/steps")
    assert r.status_code == 404


def test_replay_is_readonly_no_side_effects(monkeypatch):
    called = []
    from app.agent import nodes, llm
    monkeypatch.setattr("app.agent.llm.get_llm", lambda: (_ for _ in ()).throw(AssertionError("不应调用 LLM")))
    incident_id, run_id = _make_run_with_steps()
    r = client.get(f"/api/incidents/{incident_id}/replay/runs/{run_id}/steps")
    assert r.status_code == 200  # 不触发 LLM/MCP/状态机
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && uv run pytest tests/test_replay_api.py -q`
Expected: FAIL(404 router 不存在)

- [ ] **Step 3: replay.py**

```python
"""Replay API:只读、按 Run 限定、归属校验、后端脱敏。"""
from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.engine import get_control_engine
from app.db.models import AgentRun, IncidentReplayStep
from app.replay.integrity import check_replay_status
from app.replay.projector import ReplayProjector

router = APIRouter(prefix="/api/incidents/{incident_id}/replay", tags=["replay"])
_projector = ReplayProjector()


def _get_run_or_404(incident_id: int, agent_run_id: int) -> AgentRun:
    with Session(get_control_engine()) as s:
        run = s.get(AgentRun, agent_run_id)
        if run is None or run.incident_id != incident_id:
            raise HTTPException(404, "run not found for incident")
        return run


def _default_run_id(incident_id: int) -> int | None:
    with Session(get_control_engine()) as s:
        runs = s.scalars(select(AgentRun).where(
            AgentRun.incident_id == incident_id).order_by(
            AgentRun.finished_at.desc(), AgentRun.id.desc())).all()
        for r in runs:
            if r.finished_at is not None:
                return r.id
        return runs[0].id if runs else None


@router.get("")
def incident_manifest(incident_id: int) -> dict:
    with Session(get_control_engine()) as s:
        runs = s.scalars(select(AgentRun).where(
            AgentRun.incident_id == incident_id).order_by(AgentRun.id.asc())).all()
    return {"incidentId": incident_id,
            "runs": [{"agentRunId": r.id, "status": r.status,
                      "finishedAt": str(r.finished_at) if r.finished_at else None}
                     for r in runs],
            "defaultRunId": _default_run_id(incident_id),
            "responseSchemaVersion": "1.0"}


@router.get("/runs/{agent_run_id}")
def run_manifest(incident_id: int, agent_run_id: int) -> dict:
    run = _get_run_or_404(incident_id, agent_run_id)
    status = check_replay_status(agent_run_id)
    return {"agentRunId": agent_run_id, **status,
            "asOfSequenceNo": _as_of_sequence_no(agent_run_id),
            "sourceReplaySchemaVersion": "1.0",
            "responseSchemaVersion": "1.0",
            "playbackPolicyVersion": "1",
            "supportedSpeeds": [1, 2, 4],
            "totalSteps": None, "keyStepIndexes": None}


def _as_of_sequence_no(agent_run_id: int) -> int:
    with Session(get_control_engine()) as s:
        from sqlalchemy import func
        mx = s.scalar(select(func.max(IncidentReplayStep.sequence_no)).where(
            IncidentReplayStep.agent_run_id == agent_run_id))
        return mx or 0


@router.get("/runs/{agent_run_id}/steps")
def run_steps(incident_id: int, agent_run_id: int) -> dict:
    _get_run_or_404(incident_id, agent_run_id)
    with Session(get_control_engine()) as s:
        rows = list(s.scalars(select(IncidentReplayStep).where(
            IncidentReplayStep.agent_run_id == agent_run_id).order_by(
            IncidentReplayStep.sequence_no.asc())).all())
    status = check_replay_status(agent_run_id)
    projected = _projector.project(rows)
    return {**status, **projected}


@router.get("/runs/{agent_run_id}/steps/{logical_step_id}")
def step_detail(incident_id: int, agent_run_id: int, logical_step_id: str) -> dict:
    _get_run_or_404(incident_id, agent_run_id)
    with Session(get_control_engine()) as s:
        rows = list(s.scalars(select(IncidentReplayStep).where(
            IncidentReplayStep.agent_run_id == agent_run_id,
            IncidentReplayStep.logical_step_id == logical_step_id).order_by(
            IncidentReplayStep.sequence_no.asc())).all())
    if not rows:
        raise HTTPException(404, "step not found")
    # 技术详情懒加载:脱敏参数 + MCP/PromQL/Trace 引用 + 版本信息
    terminal = next((x for x in rows if x.phase in ("completed", "failed")), rows[0])
    return {"logicalStepId": logical_step_id,
            "decision": terminal.decision_json or (rows[0].decision_json or {}),
            "operation": terminal.operation_json or {},
            "sourceReferences": terminal.source_references_json or {},
            "versions": {"policyBundle": terminal.policy_bundle_version,
                         "prompt": terminal.prompt_version,
                         "toolContract": terminal.tool_contract_version,
                         "normalizer": terminal.normalization_rule_version,
                         "replaySchema": terminal.replay_schema_version},
            "snapshotHash": terminal.snapshot_hash}
```

- [ ] **Step 4: main.py 挂载**

```python
from app.api import replay as replay_api
app.include_router(replay_api.router)
```

- [ ] **Step 5: 运行确认通过 + 全量回归**

Run: `cd ai-service && uv run pytest tests/test_replay_api.py tests/ -q`
Expected: 全绿

- [ ] **Step 6: 提交**

```bash
git add ai-service/app/api/replay.py ai-service/app/main.py ai-service/tests/test_replay_api.py
git commit -m "feat(replay): 只读 Replay API(Incident/单Run Manifest + steps + 单步详情,归属校验)"
```

---

### Task 10: 前端 API client + 类型 + 路由

**Files:**
- Modify: `web/src/api/types.ts`(Replay 类型)
- Modify: `web/src/api/client.ts`(replay 方法)
- Modify: `web/src/router/index.ts`(/replay 路由,query: runId/position)
- Test: `web/src/api/__tests__/replay.test.ts`

**Interfaces:**
- Consumes: `GET /api/incidents/{id}/replay` 等四个端点(Task 9)。
- Produces:
  - `web/src/api/types.ts`:`ReplayManifest / ReplayRunManifest / ReplayStep / ReplayPosition(interface 含 stepIndex/logicalStepId/sourceSequenceNos/stepState/stepOutcome/stateBefore/stateAfter/decisionSummary/operationSummary/sourceReferenceSummary/actualDurationMs/displayDurationMs/missingParts)`
  - `client.ts`:`fetchIncidentReplay(incidentId) / fetchRunManifest(incidentId, runId) / fetchReplaySteps(incidentId, runId) / fetchReplayStepDetail(incidentId, runId, logicalStepId)`
  - 路由 `/replay`(query `runId`、`position`)

- [ ] **Step 1: 写失败测试**

`web/src/api/__tests__/replay.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fetchReplaySteps } from '../client'

describe('replay api client', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('fetchReplaySteps 请求正确 URL 并解析', async () => {
    const fake = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ totalSteps: 1, steps: [{
        stepIndex: 0, logicalStepId: 'a', sourceSequenceNos: [1, 2],
        stepState: 'completed', stepOutcome: 'succeeded',
        stateBefore: { facts: {} }, stateAfter: { facts: { F_INDEX_MISSING: true } },
        decisionSummary: { selectedTool: 'get_trace' }, operationSummary: {},
        sourceReferenceSummary: {}, actualDurationMs: 15, displayDurationMs: 3000,
        missingParts: [] }] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    const out = await fetchReplaySteps(123, 456)
    expect(fake).toHaveBeenCalledWith('/api/incidents/123/replay/runs/456/steps', expect.anything())
    expect(out.totalSteps).toBe(1)
    expect(out.steps[0].stateAfter.facts.F_INDEX_MISSING).toBe(true)
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd web && npx vitest run src/api/__tests__/replay.test.ts`
Expected: FAIL(fetchReplaySteps 不存在)

- [ ] **Step 3: types.ts 追加**

```typescript
export interface ReplayStep {
  stepIndex: number
  logicalStepId: string
  sourceSequenceNos: number[]
  stepState: 'completed' | 'incomplete' | 'failed' | 'started'
  stepOutcome: string | null
  stepType: string
  stepTitle: string | null
  stateBefore: Record<string, unknown> | null
  stateAfter: Record<string, unknown> | null
  missingParts: string[]
  decisionSummary: Record<string, unknown>
  operationSummary: Record<string, unknown>
  sourceReferenceSummary: Record<string, unknown>
  actualDurationMs: number
  displayDurationMs: number
}

export interface ReplayRunManifest {
  agentRunId: number
  replayStatus: 'complete' | 'partial' | 'partial_legacy' | 'in_progress' | 'unsupported' | 'unavailable'
  runStatus: string
  runOutcome: string | null
  terminationReason: string | null
  asOfSequenceNo: number
  totalSteps: number | null
  keyStepIndexes: Record<string, number> | null
}

export interface IncidentReplayManifest {
  incidentId: number
  runs: Array<{ agentRunId: number; status: string; finishedAt: string | null }>
  defaultRunId: number | null
}

export interface ReplayStepsResponse {
  replayStatus: string
  totalSteps: number
  keyStepIndexes: Record<string, number> | null
  steps: ReplayStep[]
}
```

- [ ] **Step 4: client.ts 追加**

```typescript
import type { IncidentReplayManifest, ReplayRunManifest, ReplayStepsResponse } from './types'

export async function fetchIncidentReplay(incidentId: number): Promise<IncidentReplayManifest> {
  const r = await apiFetch(`/api/incidents/${incidentId}/replay`)
  return r.json()
}

export async function fetchRunManifest(incidentId: number, runId: number): Promise<ReplayRunManifest> {
  const r = await apiFetch(`/api/incidents/${incidentId}/replay/runs/${runId}`)
  return r.json()
}

export async function fetchReplaySteps(incidentId: number, runId: number): Promise<ReplayStepsResponse> {
  const r = await apiFetch(`/api/incidents/${incidentId}/replay/runs/${runId}/steps`)
  return r.json()
}

export async function fetchReplayStepDetail(
  incidentId: number, runId: number, logicalStepId: string,
): Promise<Record<string, unknown>> {
  const r = await apiFetch(
    `/api/incidents/${incidentId}/replay/runs/${runId}/steps/${encodeURIComponent(logicalStepId)}`)
  return r.json()
}
```

> `apiFetch` 为 client.ts 既有封装的 GET 请求函数(带鉴权头/错误处理);若当前不存在,新增 `apiFetch(url)` 封装 `fetch(url, { credentials: 'same-origin' })` 并统一处理非 2xx。

- [ ] **Step 5: router 追加**

```typescript
{ path: '/replay', name: 'replay', component: () => import('../views/ReplayView.vue'),
  props: (route: { query: { runId?: string; position?: string } }) => ({
    runId: route.query.runId ? Number(route.query.runId) : undefined,
    position: route.query.position ? Number(route.query.position) : 0,
  }) }
```

- [ ] **Step 6: 运行确认通过 + 提交**

Run: `cd web && npx vitest run src/api/__tests__/replay.test.ts`
Expected: PASS

```bash
git add web/src/api/types.ts web/src/api/client.ts web/src/router/index.ts web/src/api/__tests__/replay.test.ts
git commit -m "feat(web): Replay API client + 类型 + /replay 路由(runId/position)"
```

---

### Task 11: 回放播放引擎(useReplayPlayback)

**Files:**
- Create: `web/src/composables/useReplayPlayback.ts`
- Create: `web/src/composables/__tests__/useReplayPlayback.test.ts`

**Interfaces:**
- Consumes: `ReplayStep[]`(Task 10 类型)。
- Produces:
  - `useReplayPlayback(steps: Ref<ReplayStep[]>)` 返回:
    - `position: Ref<number>`(0..steps.length,状态位置)
    - `playbackState: Ref<'IDLE'|'PLAYING'|'PAUSED'|'COMPLETED'|'ERROR'>`
    - `speed: Ref<1|2|4>`
    - `play() / pause() / toggle() / next() / prev() / seekTo(position) / jumpTo(keyStep) / restart()`
    - `currentStep: ComputedRef<ReplayStep | null>`(position-1,无则 null)
    - `displayStep: ComputedRef<{ before: Record| null; after: Record | null }>`(position=0 → steps[0].stateBefore;position=N → steps[N-1].stateAfter)
    - `onVisibilityHidden()`(页面隐藏时 pause)

- [ ] **Step 1: 写失败测试(用 vi.useFakeTimers)**

`web/src/composables/__tests__/useReplayPlayback.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ref } from 'vue'
import { useReplayPlayback } from '../useReplayPlayback'

const steps = () => ([
  { stepIndex: 0, displayDurationMs: 1000 } as any,
  { stepIndex: 1, displayDurationMs: 2000 } as any,
  { stepIndex: 2, displayDurationMs: 500 } as any,
])

describe('useReplayPlayback', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('初始 IDLE, position=0, 显示第 0 步 stateBefore', () => {
    const { position, playbackState, displayStep } = useReplayPlayback(ref(steps()))
    expect(position.value).toBe(0)
    expect(playbackState.value).toBe('IDLE')
  })

  it('播放后按 displayDurationMs 推进 position', () => {
    const { position, playbackState, play } = useReplayPlayback(ref(steps()))
    play()
    expect(playbackState.value).toBe('PLAYING')
    vi.advanceTimersByTime(1000)
    expect(position.value).toBe(1)
    vi.advanceTimersByTime(2000)
    expect(position.value).toBe(2)
    vi.advanceTimersByTime(500)
    expect(position.value).toBe(3)
    expect(playbackState.value).toBe('COMPLETED')
  })

  it('手动跳转后自动暂停', () => {
    const { position, playbackState, play, seekTo } = useReplayPlayback(ref(steps()))
    play()
    vi.advanceTimersByTime(1000)
    seekTo(2)
    expect(position.value).toBe(2)
    expect(playbackState.value).toBe('PAUSED')
  })

  it('切换倍速不改变当前步骤, 且重新计时', () => {
    const { position, speed, play, setSpeed } = useReplayPlayback(ref(steps()))
    play()
    vi.advanceTimersByTime(1000)
    setSpeed(2)
    expect(speed.value).toBe(2)
    expect(position.value).toBe(1)
    vi.advanceTimersByTime(1000)  // 2000/2 = 1000ms 进入下一步
    expect(position.value).toBe(2)
  })

  it('上一步/下一步后进入 PAUSED', () => {
    const { position, playbackState, play, next, prev } = useReplayPlayback(ref(steps()))
    play()
    vi.advanceTimersByTime(1000)
    next()
    expect(position.value).toBe(2)
    expect(playbackState.value).toBe('PAUSED')
    prev()
    expect(position.value).toBe(1)
  })

  it('restart 回到 position=0', () => {
    const { position, play, restart, playbackState } = useReplayPlayback(ref(steps()))
    play()
    vi.advanceTimersByTime(1000)
    restart()
    expect(position.value).toBe(0)
    expect(playbackState.value).toBe('IDLE')
  })

  it('只存在一个计时器(seek 取消旧 Timer)', () => {
    const { play, seekTo } = useReplayPlayback(ref(steps()))
    play()
    vi.advanceTimersByTime(500)
    seekTo(1)
    vi.advanceTimersByTime(1000)
    expect(vi.getTimerCount()).toBeLessThanOrEqual(1)
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd web && npx vitest run src/composables/__tests__/useReplayPlayback.test.ts`
Expected: FAIL(useReplayPlayback 不存在)

- [ ] **Step 3: useReplayPlayback.ts**

```typescript
import { computed, onUnmounted, ref, watch } from 'vue'
import type { ReplayStep } from '../api/types'

export type PlaybackState = 'IDLE' | 'PLAYING' | 'PAUSED' | 'COMPLETED' | 'ERROR'
export type Speed = 1 | 2 | 4

export function useReplayPlayback(steps: { value: ReplayStep[] }) {
  const position = ref(0)
  const playbackState = ref<PlaybackState>('IDLE')
  const speed = ref<Speed>(1)
  let timer: ReturnType<typeof setTimeout> | null = null

  const currentStep = computed(() =>
    position.value >= 1 ? steps.value[position.value - 1] ?? null : null)

  const displayStep = computed(() => {
    if (position.value === 0) {
      return { before: steps.value[0]?.stateBefore ?? null, after: null }
    }
    const st = steps.value[position.value - 1]
    return { before: st?.stateBefore ?? null, after: st?.stateAfter ?? null }
  })

  function clearTimer() {
    if (timer !== null) { clearTimeout(timer); timer = null }
  }

  function scheduleNext() {
    clearTimer()
    if (position.value >= steps.value.length) {
      playbackState.value = 'COMPLETED'
      return
    }
    const st = steps.value[position.value]
    const ms = Math.max(50, Math.round((st?.displayDurationMs ?? 2000) / speed.value))
    timer = setTimeout(() => {
      position.value += 1
      if (position.value >= steps.value.length) {
        playbackState.value = 'COMPLETED'
      } else {
        scheduleNext()
      }
    }, ms)
  }

  function play() {
    if (playbackState.value === 'COMPLETED') restart()
    if (position.value >= steps.value.length) return
    playbackState.value = 'PLAYING'
    scheduleNext()
  }

  function pause() { clearTimer(); if (playbackState.value === 'PLAYING') playbackState.value = 'PAUSED' }

  function toggle() { playbackState.value === 'PLAYING' ? pause() : play() }

  function seekTo(target: number) {
    clearTimer()
    position.value = Math.max(0, Math.min(steps.value.length, target))
    playbackState.value = 'PAUSED'
  }

  function next() {
    if (position.value < steps.value.length) seekTo(position.value + 1)
  }

  function prev() { if (position.value > 0) seekTo(position.value - 1) }

  function jumpTo(keyStep: number | undefined) {
    if (keyStep !== undefined) seekTo(keyStep + 1)  // keyStepIndexes 是步骤下标, 状态位置 = 下标+1
  }

  function setSpeed(s: Speed) {
    speed.value = s
    if (playbackState.value === 'PLAYING') scheduleNext()  // 切换倍速重新计时
  }

  function restart() { clearTimer(); position.value = 0; playbackState.value = 'IDLE' }

  function onVisibilityHidden() { pause() }

  watch(steps, () => { if (position.value > steps.value.length) position.value = steps.value.length })

  onUnmounted(clearTimer)
  document.addEventListener('visibilitychange', onVisibilityHidden)

  return {
    position, playbackState, speed, currentStep, displayStep,
    play, pause, toggle, next, prev, seekTo, jumpTo, setSpeed, restart, onVisibilityHidden,
  }
}
```

- [ ] **Step 4: 运行确认通过 + 提交**

Run: `cd web && npx vitest run src/composables/__tests__/useReplayPlayback.test.ts`
Expected: 7 passed

```bash
git add web/src/composables/useReplayPlayback.ts web/src/composables/__tests__/useReplayPlayback.test.ts
git commit -m "feat(web): useReplayPlayback — position 语义/状态机/单次 setTimeout/倍速/跳转暂停"
```

---

### Task 12: ReplayView 页面 + 详情页入口

**Files:**
- Create: `web/src/views/ReplayView.vue`
- Create: `web/src/views/ReplayView.test.ts`
- Modify: `web/src/views/IncidentDetailView.vue`(加入口"查看历史回放")

**Interfaces:**
- Consumes: `fetchIncidentReplay/fetchRunManifest/fetchReplaySteps/fetchReplayStepDetail`(Task 10)、`useReplayPlayback`(Task 11)。
- Produces: 回放页(时间轴 + 左侧状态快照 + 右侧详情 + 控制条 + 技术详情折叠 + partial 占位 + 只读提示)。

- [ ] **Step 1: 写失败测试(组件渲染 + 交互)**

`web/src/views/ReplayView.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import ReplayView from './ReplayView.vue'
import * as api from '../api/client'

const steps = [
  { stepIndex: 0, logicalStepId: 'a', stepState: 'completed', stepOutcome: 'succeeded',
    stepType: 'INCIDENT_INGESTED', stepTitle: '事件接入',
    stateBefore: { facts: {}, hypotheses: [] }, stateAfter: { facts: {}, hypotheses: [] },
    decisionSummary: {}, operationSummary: {}, sourceReferenceSummary: {},
    actualDurationMs: 10, displayDurationMs: 1000, missingParts: [], sourceSequenceNos: [1, 2] },
  { stepIndex: 1, logicalStepId: 'b', stepState: 'incomplete', stepOutcome: null,
    stepType: 'FIX_EXECUTED', stepTitle: '修复执行',
    stateBefore: { facts: {} }, stateAfter: null,
    decisionSummary: {}, operationSummary: {}, sourceReferenceSummary: {},
    actualDurationMs: 0, displayDurationMs: 2500, missingParts: ['stateAfter', 'operationResult'],
    sourceSequenceNos: [3] },
]

describe('ReplayView', () => {
  beforeEach(() => {
    vi.spyOn(api, 'fetchIncidentReplay').mockResolvedValue({
      incidentId: 1, runs: [{ agentRunId: 10, status: 'completed', finishedAt: 'x' }], defaultRunId: 10 } as any)
    vi.spyOn(api, 'fetchRunManifest').mockResolvedValue({
      agentRunId: 10, replayStatus: 'partial', runStatus: 'terminated',
      runOutcome: 'needs_human', terminationReason: 'no_progress',
      asOfSequenceNo: 3, totalSteps: 2, keyStepIndexes: {} } as any)
    vi.spyOn(api, 'fetchReplaySteps').mockResolvedValue({
      replayStatus: 'partial', totalSteps: 2, keyStepIndexes: {}, steps } as any)
  })

  it('渲染只读提示、时间轴与控制条', async () => {
    const wrapper = mount(ReplayView, { props: { incidentId: 1 } })
    await wrapper.vm.$nextTick(); await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('历史回放')
    expect(wrapper.text()).toContain('只读')
    expect(wrapper.text()).toContain('partial')
    expect(wrapper.find('[data-testid="replay-play"]').exists()).toBe(true)
  })

  it('incomplete 步骤显示缺失标记', async () => {
    const wrapper = mount(ReplayView, { props: { incidentId: 1 } })
    await wrapper.vm.$nextTick(); await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('工具调用结果缺失')
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd web && npx vitest run src/views/ReplayView.test.ts`
Expected: FAIL(ReplayView 不存在)

- [ ] **Step 3: ReplayView.vue(核心结构)**

```vue
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchIncidentReplay, fetchRunManifest, fetchReplaySteps, fetchReplayStepDetail } from '../api/client'
import type { IncidentReplayManifest, ReplayRunManifest, ReplayStep, ReplayStepsResponse } from '../api/types'
import { useReplayPlayback } from '../composables/useReplayPlayback'

const props = defineProps<{ incidentId: number; runId?: number; position?: number }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const manifest = ref<IncidentReplayManifest | null>(null)
const runManifest = ref<ReplayRunManifest | null>(null)
const steps = ref<ReplayStep[]>([])
const activeRunId = ref(props.runId ?? 0)
const techDetail = ref<Record<string, unknown> | null>(null)
const showTech = ref(false)

const { position, playbackState, speed, displayStep, currentStep,
        play, pause, toggle, next, prev, seekTo, jumpTo, setSpeed, restart } =
  useReplayPlayback(steps as any)

onMounted(async () => {
  manifest.value = await fetchIncidentReplay(props.incidentId)
  activeRunId.value = props.runId ?? manifest.value.defaultRunId ?? 0
  if (activeRunId.value) {
    runManifest.value = await fetchRunManifest(props.incidentId, activeRunId.value)
    const resp: ReplayStepsResponse = await fetchReplaySteps(props.incidentId, activeRunId.value)
    steps.value = resp.steps
    if (props.position) seekTo(props.position)
  }
})

async function loadStepDetail(step: ReplayStep) {
  showTech.value = true
  techDetail.value = await fetchReplayStepDetail(props.incidentId, activeRunId.value, step.logicalStepId)
}
</script>

<template>
  <div class="replay-view" data-testid="replay-view">
    <div class="replay-banner">历史回放 · 只读 · 不会执行任何系统操作</div>
    <div class="replay-status" v-if="runManifest">
      {{ runManifest.replayStatus }} · runOutcome={{ runManifest.runOutcome }}
      <span v-if="runManifest.replayStatus === 'partial'">部分审计记录缺失</span>
    </div>
    <!-- 顶部时间轴 -->
    <div class="timeline">
      <button v-for="st in steps" :key="st.logicalStepId"
              :class="['tl-node', { active: currentStep?.logicalStepId === st.logicalStepId,
                                    incomplete: st.stepState === 'incomplete' }]"
              :data-testid="`tl-${st.logicalStepId}`"
              @click="jumpTo(st.stepIndex)">
        {{ st.stepTitle || st.stepType }}
        <span v-if="st.stepState === 'incomplete'" class="missing-flag">缺失</span>
      </button>
    </div>
    <div class="replay-body">
      <!-- 左侧:状态快照 -->
      <aside class="state-panel">
        <section>
          <h4>假设</h4>
          <ul>
            <li v-for="h in (displayStep.after?.hypotheses ?? displayStep.before?.hypotheses ?? [])"
                :key="h.id">
              <span :class="['state-chip', h.status]">{{ h.status }}</span>{{ h.description }}
            </li>
          </ul>
        </section>
        <section>
          <h4>共享 Facts</h4>
          <ul>
            <li v-for="(v, k) in (displayStep.after?.facts ?? displayStep.before?.facts ?? {})"
                :key="k">
              <span :class="['state-chip', String(v)]">{{ v }}</span>{{ k }}
            </li>
          </ul>
        </section>
        <section>
          <h4>排他条件</h4>
          <ul>
            <li v-for="(v, k) in (displayStep.after?.exclusion_conditions
                                  ?? displayStep.before?.exclusion_conditions ?? {})" :key="k">
              <span :class="['state-chip', String(v)]">{{ v }}</span>{{ k }}
            </li>
          </ul>
        </section>
      </aside>
      <!-- 右侧:本步详情 -->
      <main class="detail-panel">
        <div class="step-summary">
          <h3>{{ currentStep?.stepTitle || '初始状态' }}</h3>
          <p v-if="currentStep?.missingParts?.length" class="missing-flag">
            工具调用结果缺失({{ currentStep.missingParts.join(', ') }})</p>
          <pre class="decision">{{ JSON.stringify(currentStep?.decisionSummary ?? {}, null, 2) }}</pre>
        </div>
        <div class="controls">
          <button data-testid="replay-play" @click="toggle">{{ playbackState === 'PLAYING' ? '暂停' : '播放' }}</button>
          <button @click="prev">上一步</button>
          <button @click="next">下一步</button>
          <button v-for="s in [1, 2, 4]" :key="s" @click="setSpeed(s as any)">{{ s }}×</button>
          <button @click="restart">重新播放</button>
          <button v-if="currentStep" @click="loadStepDetail(currentStep)">技术详情</button>
        </div>
        <details v-if="showTech && techDetail" class="tech-detail">
          <summary>技术详情</summary>
          <pre>{{ JSON.stringify(techDetail, null, 2) }}</pre>
        </details>
      </main>
    </div>
  </div>
</template>

<style scoped>
.state-chip { padding: 1px 6px; border-radius: 8px; font-size: 12px; margin-right: 6px; }
.state-chip.true, .state-chip.supported, .state-chip.confirmed { background: #d1f0ff; color: #0a5c8a; }
.state-chip.false, .state-chip.refuted { background: #ffe3e3; color: #a13; }
.state-chip.unknown, .state-chip.proposed { background: #eee; color: #666; }
.state-chip.stale { background: #fff2d9; color: #9a6a00; }
.state-chip.conflict { background: #ffd9d9; color: #c00; }
.missing-flag { color: #c00; font-weight: 600; margin-left: 6px; }
.tl-node.incomplete { border-color: #c00; }
</style>
```

- [ ] **Step 4: IncidentDetailView 加入口**

详情页头部(返回/操作区)追加:

```vue
<el-button v-if="incident.id" @click="$router.push({
  path: '/replay', query: { incidentId: incident.id } })">
  查看历史回放
</el-button>
```

> 回放页需**关闭 Incident SSE 与详情页轮询**:进入 /replay 路由时 IncidentDetailView 卸载(SSE/轮询在组件卸载时清理,既有实现已具备);若详情页轮询在全局 store 中,需在路由守卫或 ReplayView mounted 中停止。实现时确认 `useIncidentStream`/轮询的清理时机,保证进入回放页后不再发非 Replay GET。

- [ ] **Step 5: 运行确认通过**

Run: `cd web && npx vitest run src/views/ReplayView.test.ts && timeout 180 npm run build`
Expected: 2 passed + build 通过

- [ ] **Step 6: 提交**

```bash
git add web/src/views/ReplayView.vue web/src/views/ReplayView.test.ts web/src/views/IncidentDetailView.vue
git commit -m "feat(web): ReplayView 回放页(时间轴/状态快照/控制条/partial 占位)+ 详情页入口"
```

---

### Task 13: E2E 验收 + 只读无副作用验证

**Files:**
- Create: `scripts/verify-m15.py`
- Test: `ai-service/tests/test_replay_e2e.py`(可选进程内)

**Interfaces:**
- Consumes: 全栈运行(Java + ai + MySQL);SCN-001/SCN-002 场景;Replay API(Task 9)。
- Produces: `verify-m15.py` 验收脚本。

- [ ] **Step 1: 写验收脚本 `scripts/verify-m15.py`**

流程:
1. `reset(SCN-001)` → 健康负载 → create incident → inject → investigations → 审批 → 恢复(复用 verify-m14 的步骤)
2. 断言 `GET /api/incidents/{id}/replay`:
   - `replayStatus == "complete"`(SCN-001 正常闭环)
   - `totalSteps >= 8`(ingest/hypothesize/evidence×N/diagnose/fix_proposed/approval_requested/approval_decided/fix_executed/recovery_verified/report/run_terminated)
   - `keyStepIndexes` 含 diagnosis/approval/execution/recovery
   - 每步 `stepState in ("completed","failed")` 且 `sourceSequenceNos` 非空
   - `snapshotHash` 长度 64(单步详情接口)
3. 只读无副作用断言:
   - 播放期间**仅 GET**:记录调查前后 `Incident / Approval / FixExecution` 的 id 集合与字段,调用 Replay 全部端点后比对**完全一致**
   - 重复 `GET .../steps` 两次 → 步骤顺序与各步 stateAfter JSON 一致
   - `runId` 归属:用错误 incident_id 访问 → 404
   - 断点:进入回放只发 Replay GET(由前端 E2E 或手动 DevTools 验证;脚本验证后端侧无副作用)
4. `reset(SCN-002)` 跑锁场景一轮 → `replayStatus == "complete"`(锁根因路径)
5. rejected 路径:创建 incident → 调查 → 审批拒绝 → `runOutcome == "rejected"` 且 **无 FIX_EXECUTED 必需步骤要求**(`keyStepIndexes` 不含 execution 或允许缺失)

- [ ] **Step 2: 运行验收**

Run: `cd D:\wendang\TraceMind && python scripts/verify-m15.py --base http://localhost:8000 --order http://localhost:8081`
Expected: PASS(需要本地或 VM 全栈运行;SCN-001 + SCN-002 complete Replay + rejected 路径)

- [ ] **Step 3: 全量回归**

Run: `cd ai-service && uv run pytest tests/ -q && cd ../web && npx vitest run && npm run build`
Expected: 全绿

- [ ] **Step 4: 提交**

```bash
git add scripts/verify-m15.py ai-service/tests/test_replay_e2e.py
git commit -m "feat(e2e): verify-m15 — 双场景 complete Replay + rejected 路径 + 只读无副作用断言"
```

---

### Task 14: 版本恢复校验(version_mismatch)

**Files:**
- Modify: `ai-service/app/services/runner.py`(Run 恢复前校验)
- Modify: `ai-service/app/agent/graph.py`(RUN_TERMINATED 已含;补 version_mismatch 终态)
- Test: `ai-service/tests/test_version_mismatch.py`

**Interfaces:**
- Consumes: `AgentRun.expected_policy_bundle_version`(Task 1)、`POLICY_BUNDLE_VERSION`(Task 1)。
- Produces: Run 恢复时若 `expected_policy_bundle_version != POLICY_BUNDLE_VERSION` → 停止原 Run(状态 `version_mismatch`),不继续执行;审批等待期间部署新版本后恢复,走 version_mismatch 而非用新 Policy 继续。

- [ ] **Step 1: 写失败测试**

`ai-service/tests/test_version_mismatch.py`:

```python
from app.services import runner


def test_run_resume_checks_expected_version(monkeypatch):
    calls = {}
    def fake_resume(run):
        calls["checked"] = run.expected_policy_bundle_version
    monkeypatch.setattr("app.services.runner._resume_inner", fake_resume)
    out = runner.resume_investigation(incident_id=999008, run_id=999008,
                                      thread_id="t-vm")
    # 期望:版本不一致时进入 version_mismatch,不调用 _resume_inner
    assert "version_mismatch" in out
    assert "checked" not in calls
```

- [ ] **Step 2: 运行确认失败**

Run: `cd ai-service && uv run pytest tests/test_version_mismatch.py -q`
Expected: FAIL

- [ ] **Step 3: runner.resume_investigation 增加校验**

在 `resume_investigation`(审批恢复入口)开头:

```python
from app.replay.versions import POLICY_BUNDLE_VERSION
from app.db.models import AgentRun
from sqlalchemy.orm import Session
from app.db.engine import get_control_engine


def _assert_run_version(run: AgentRun) -> str | None:
    expected = run.expected_policy_bundle_version
    if expected and expected != POLICY_BUNDLE_VERSION:
        return "version_mismatch"
    return None
```

恢复前调用;非 None → 更新 run.status 并返回,不执行恢复逻辑。

- [ ] **Step 4: 运行确认通过 + 全量回归**

Run: `cd ai-service && uv run pytest tests/test_version_mismatch.py tests/ -q`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/services/runner.py ai-service/app/agent/graph.py ai-service/tests/test_version_mismatch.py
git commit -m "feat(replay): Run 恢复前版本校验 — 不一致停止原 Run(version_mismatch)"
```
