# TraceMind M2:AI 服务与工具层 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建 Python AI 服务(FastAPI),实现控制库 13 表、三连接池四账号、Incident 创建与 digest 基线、七个受控工具、审计与 IncidentEvent、基础 API 与演示场景代理——验收:不调用 LLM 也能手动取齐 E1~E5 诊断证据。

**Architecture:** `ai-service/` 独立 Python 服务,同步 SQLAlchemy 2.0 + pymysql(三个 engine 对应三连接池),httpx 调用 M1 的 Java 服务;工具层为"注册表 + 白名单 schema + 统一返回外壳",每个工具独立服务模块;审计与事件写入 `tracemind_control` 库。

**Tech Stack:** Python 3.12+、FastAPI、Pydantic v2、SQLAlchemy 2.0、httpx、uv、pytest;依赖 M1 的 Java 观测端点与 `performance_schema`。

## Global Constraints

- 三连接池:control(`tracemind_control_app`)/ readonly(`ai_investigator`)/ executor(`fix_executor`);普通代码只碰 control,调查工具只碰 readonly,`execute_fix` Action 只碰 executor。
- 控制库 13 表(名称/字段与设计文档一致):`incident` / `agent_run` / `hypothesis` / `evidence` / `hypothesis_evidence` / `tool_call` / `fix_definition` / `fix_proposal` / `approval` / `fix_execution` / `recovery_check` / `postmortem` / `incident_event`。
- 工具白名单:查询 `get_query_plan` 仅 `INVENTORY_LOOKUP`;表 `get_index_info` 仅 `inventory`;服务 `get_service_metrics` 仅 `order-service` / `inventory-service`。
- 统一返回外壳:`{tool_call_id, success, observed_at, duration_ms, data, error_code, error_message}`;每次工具调用写 `tool_call` + `incident_event`(审计)。
- digest 基线:`agent_run.incident_digest_baseline` 存 Incident 创建时的 `events_statements_summary_by_digest` 快照;`list_expensive_query_digests` 返回当前值减基线。
- 环境变量(全部可配,不写死):`CONTROL_DB_URL`、`BUSINESS_DB_URL`、`READONLY_DB_URL`(可选,默认取自 BUSINESS_DB_URL 换账号)、`ORDER_SERVICE_URL`、`INVENTORY_SERVICE_URL`、`AI_SERVICE_URL`、`DEMO_MODE`、`DEMO_KEY`、`LLM_MODE`。
- Java 端点(来自 M1):`GET /internal/observations/metrics?window_seconds=`、`GET /internal/observations/traces/{traceId}`(404=TRACE_NOT_FOUND)、`POST /internal/scenarios/SCN-001/{inject|reset}`、`GET .../status`(DEMO_KEY 经 `x-demo-key`)。
- 控制库初始化:新增 `scripts/sql/04-control-schema.sql`(幂等),并入 `init-database.ps1`。

## File Structure

```
ai-service/
  pyproject.toml                     # uv 管理:fastapi/uvicorn/sqlalchemy/pymysql/httpx/pydantic-settings
  app/
    __init__.py
    main.py                          # FastAPI 入口(挂路由,DEMO 路由按 DEMO_MODE 条件注册)
    config.py                        # Settings(pydantic-settings,环境变量)
    db/
      __init__.py
      engine.py                      # 三连接池 engine 工厂
      models.py                      # SQLAlchemy 2.0 映射(13 表)
    repositories/
      __init__.py
      incident_repo.py               # incident CRUD + digest 基线写入
      run_repo.py                    # agent_run CRUD
      tool_repo.py                   # tool_call 写入
      event_repo.py                  # incident_event 写入 + 序列号
    services/
      __init__.py
      baseline_service.py            # performance_schema digest 快照
      java_client.py                 # httpx 调 Java 观测/场景端点
      metrics_service.py             # get_service_metrics
      trace_service.py               # get_trace(组合两服务)
      slow_query_service.py          # list_expensive_query_digests
      query_plan_service.py          # get_query_plan(白名单)
      index_info_service.py          # get_index_info
      fix_service.py                 # execute_fix(骨架:校验+executor 连接池)
      recovery_service.py            # verify_recovery(骨架)
    tools/
      __init__.py
      registry.py                    # 工具注册表(name → callable + schema)
      schemas.py                     # 入参/出参 Pydantic 模型 + 统一外壳
      execute.py                     # 统一执行包装(计时+审计+外壳)
    api/
      __init__.py
      incidents.py                   # /api/incidents 路由
      runs.py                        # /api/incidents/{id}/runs、investigations
      demo.py                        # /api/demo/scenarios/SCN-001/* 代理
  tests/
    __init__.py
    conftest.py                      # 测试库 fixture(独立 test schema)
    test_health.py
    test_incident_repo.py
    test_tools.py
    test_api_incidents.py
scripts/
  sql/04-control-schema.sql          # 控制库 13 表(幂等)
  verify-m2.py                       # 验收:不调 LLM 取齐 E1~E5
```

---

### Task 2.1: AI 服务骨架(FastAPI + 配置 + 健康检查)

**Files:**
- Create: `ai-service/pyproject.toml`
- Create: `ai-service/app/__init__.py`、`app/config.py`、`app/main.py`
- Create: `ai-service/tests/__init__.py`、`tests/conftest.py`、`tests/test_health.py`
- Test: `ai-service/tests/test_health.py`

**Interfaces:**
- Produces: `GET /api/health` → `{"status":"ok"}`;`app.config.Settings`(env 前缀 `TRACEMIND_`,属性 `control_db_url`、`readonly_db_url`、`order_service_url`、`inventory_service_url`、`demo_mode`、`demo_key`、`llm_mode`)。后续任务通过 `from app.config import settings` 读取。

- [ ] **Step 1: 写 pyproject.toml 并安装依赖**

```toml
[project]
name = "tracemind-ai"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "sqlalchemy>=2.0",
  "pymysql>=1.1",
  "httpx>=0.27",
  "pydantic-settings>=2.3",
]

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24"]
```

Run: `cd ai-service && uv pip install -e ".[dev]"`(若报错改用 `uv sync` 或 `pip install -e ".[dev]"`)。
Expected: 依赖安装成功。

- [ ] **Step 2: 写失败测试**

`tests/test_health.py`:

```python
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: app.main`。

- [ ] **Step 4: 写 config.py 与 main.py**

`app/config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRACEMIND_", env_file=".env.local", extra="ignore")

    control_db_url: str = "mysql+pymysql://tracemind_control_app:control_app_pwd@localhost:3306/tracemind_control"
    readonly_db_url: str = "mysql+pymysql://ai_investigator:investigator_pwd@localhost:3306/tracemind_business"
    order_service_url: str = "http://localhost:8081"
    inventory_service_url: str = "http://localhost:8082"
    demo_mode: bool = False
    demo_key: str = ""
    llm_mode: str = "fake"


settings = Settings()
```

`app/main.py`:

```python
from fastapi import FastAPI

from app.config import settings

app = FastAPI(title="TraceMind AI Service")


@app.get("/api/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_health.py -v`
Expected: PASS(1 passed)。

- [ ] **Step 6: 提交**

```bash
git add ai-service/
git commit -m "feat(ai): FastAPI 骨架 + 配置 + 健康检查"
```

---

### Task 2.2: 控制库 13 表 DDL + SQLAlchemy 模型

**Files:**
- Create: `scripts/sql/04-control-schema.sql`
- Modify: `scripts/init-database.ps1`(追加 04 执行)
- Create: `ai-service/app/db/__init__.py`、`app/db/models.py`
- Test: `ai-service/tests/test_control_schema.py`

**Interfaces:**
- Produces: `tracemind_control` 库 13 表;`app.db.models` 暴露 `Base` 与各表模型类(`Incident`、`AgentRun`、`Hypothesis`、`Evidence`、`HypothesisEvidence`、`ToolCall`、`FixDefinition`、`FixProposal`、`Approval`、`FixExecution`、`RecoveryCheck`、`Postmortem`、`IncidentEvent`)。Task 2.4/2.5 的 repository 依赖这些模型。

- [ ] **Step 1: 写 04-control-schema.sql(幂等,完整 13 表)**

`scripts/sql/04-control-schema.sql`:

```sql
-- TraceMind:控制库 13 表(幂等)
USE tracemind_control;

CREATE TABLE IF NOT EXISTS incident (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  title VARCHAR(255) NOT NULL,
  description TEXT NULL,
  severity VARCHAR(16) NOT NULL DEFAULT 'medium',
  service_ref VARCHAR(64) NULL,
  observed_at DATETIME NULL,
  trigger_trace_id VARCHAR(64) NULL,
  healthy_metrics_baseline JSON NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'created',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS agent_run (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  thread_id VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'created',
  investigation_round INT NOT NULL DEFAULT 0,
  tool_call_count INT NOT NULL DEFAULT 0,
  incident_digest_baseline JSON NULL,
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME NULL,
  UNIQUE KEY uq_run_thread (thread_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS hypothesis (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  description VARCHAR(512) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'proposed',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS evidence (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  tool_call_id VARCHAR(64) NULL,
  source VARCHAR(64) NOT NULL,
  content JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS hypothesis_evidence (
  hypothesis_id BIGINT NOT NULL,
  evidence_id BIGINT NOT NULL,
  relation VARCHAR(16) NOT NULL,
  PRIMARY KEY (hypothesis_id, evidence_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS tool_call (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NULL,
  tool_name VARCHAR(64) NOT NULL,
  input JSON NULL,
  output JSON NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'success',
  duration_ms INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS fix_definition (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  action_name VARCHAR(64) NOT NULL,
  risk_level VARCHAR(16) NOT NULL DEFAULT 'medium',
  description VARCHAR(512) NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS fix_proposal (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  fix_definition_id BIGINT NOT NULL,
  parameters_json JSON NULL,
  parameters_hash VARCHAR(64) NOT NULL,
  risk_level VARCHAR(16) NOT NULL DEFAULT 'medium',
  reason VARCHAR(512) NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'proposed',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS approval (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  fix_proposal_id BIGINT NOT NULL,
  action_type VARCHAR(64) NOT NULL,
  parameters_hash VARCHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  approver VARCHAR(64) NULL,
  comment VARCHAR(512) NULL,
  expires_at DATETIME NULL,
  consumed_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS fix_execution (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  fix_proposal_id BIGINT NOT NULL,
  approval_id BIGINT NOT NULL,
  idempotency_key VARCHAR(128) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  result JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_fix_idem (idempotency_key)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS recovery_check (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  fix_execution_id BIGINT NULL,
  index_present TINYINT(1) NULL,
  query_plan_uses_target_index TINYINT(1) NULL,
  estimated_rows_before BIGINT NULL,
  estimated_rows_after BIGINT NULL,
  latency_p95_before BIGINT NULL,
  latency_p95_after BIGINT NULL,
  consecutive_healthy_checks INT NOT NULL DEFAULT 0,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS postmortem (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  content JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS incident_event (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_id BIGINT NOT NULL,
  sequence INT NOT NULL,
  event_type VARCHAR(32) NOT NULL,
  payload JSON NULL,
  occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_incident_seq (incident_id, sequence),
  KEY idx_event_incident (incident_id, id)
) ENGINE=InnoDB;
```

> 实施时补全全部 13 表,字段与设计文档 6.2 表格一一对应。

- [ ] **Step 2: 修改 init-database.ps1 追加 04 执行**

在 `03-schema.sql` 之后追加:

```powershell
Write-Host "==> control schema DDL (idempotent)"
& mysql @mysqlArgs "source $(Join-Path $sqlDir '04-control-schema.sql')"
if ($LASTEXITCODE -ne 0) { throw "failed to create control tables" }
```

- [ ] **Step 3: 执行并验证表存在**

Run: `export MYSQL_ROOT_PASSWORD=root && powershell -ExecutionPolicy Bypass -File scripts/init-database.ps1`
Run:

```bash
mysql -utracemind_control_app -pcontrol_app_pwd tracemind_control -e "SHOW TABLES;" 2>/dev/null
```

Expected: 13 张表列出。

- [ ] **Step 4: 写 SQLAlchemy 模型(节选)**

`app/db/models.py`(SQLAlchemy 2.0 声明式,字段与 DDL 一致;以下为核心表,实施时补全):

```python
from datetime import datetime
from sqlalchemy import BigInteger, DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Incident(Base):
    __tablename__ = "incident"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    service_ref: Mapped[str | None] = mapped_column(String(64))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime)
    trigger_trace_id: Mapped[str | None] = mapped_column(String(64))
    healthy_metrics_baseline: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="created")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
```

- [ ] **Step 5: 写 schema 存在性测试**

`tests/test_control_schema.py`(用控制库连接池):

```python
from sqlalchemy import create_engine, inspect
from app.config import settings

engine = create_engine(settings.control_db_url)

EXPECTED_TABLES = {
    "incident", "agent_run", "hypothesis", "evidence", "hypothesis_evidence",
    "tool_call", "fix_definition", "fix_proposal", "approval", "fix_execution",
    "recovery_check", "postmortem", "incident_event",
}


def test_control_schema_tables_exist():
    tables = set(inspect(engine).get_table_names())
    missing = EXPECTED_TABLES - tables
    assert not missing, f"missing tables: {missing}"
```

- [ ] **Step 6: 运行测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_control_schema.py -v`
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add scripts/sql/04-control-schema.sql scripts/init-database.ps1 ai-service/app/db/
git commit -m "feat(ai): 控制库 13 表 DDL + SQLAlchemy 模型"
```

---

### Task 2.3: 三连接池 + 四账号隔离

**Files:**
- Create: `ai-service/app/db/engine.py`
- Test: `ai-service/tests/test_engines.py`

**Interfaces:**
- Produces: `get_control_engine()` / `get_readonly_engine()` / `get_executor_engine()`,各返回独立 `sqlalchemy.Engine`(缓存单例);`get_executor_engine()` 仅被 `execute_fix` 使用。

- [ ] **Step 1: 写失败测试**

`tests/test_engines.py`:

```python
from sqlalchemy import text
from app.db.engine import get_control_engine, get_readonly_engine


def test_control_engine_can_write():
    engine = get_control_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE TEMPORARY TABLE _t (id INT)"))
        conn.execute(text("INSERT INTO _t VALUES (1)"))


def test_readonly_engine_cannot_write():
    engine = get_readonly_engine()
    with engine.connect() as conn:
        try:
            conn.execute(text("CREATE TEMPORARY TABLE _t2 (id INT)"))
            raise AssertionError("readonly engine must reject writes")
        except Exception:
            pass  # 期望权限/语法拒绝
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_engines.py -v`
Expected: FAIL — `ModuleNotFoundError: app.db.engine`。

- [ ] **Step 3: 写 engine.py**

```python
from functools import lru_cache
from sqlalchemy import create_engine, Engine

from app.config import settings


@lru_cache
def get_control_engine() -> Engine:
    return create_engine(settings.control_db_url, pool_pre_ping=True)


@lru_cache
def get_readonly_engine() -> Engine:
    return create_engine(settings.readonly_db_url, pool_pre_ping=True)


@lru_cache
def get_executor_engine() -> Engine:
    # fix_executor 连接的是 business 库(只有目标表 INDEX 权限)
    executor_url = settings.control_db_url.replace(
        "tracemind_control_app:control_app_pwd", "fix_executor:fix_executor_pwd"
    ).replace("tracemind_control", "tracemind_business")
    return create_engine(executor_url, pool_pre_ping=True)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_engines.py -v`
Expected: PASS(若 readonly 权限配置正确)。

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/db/engine.py ai-service/tests/test_engines.py
git commit -m "feat(ai): 三连接池(control/readonly/executor)账号隔离"
```

---

### Task 2.4: Incident 创建 + digest 基线采集

**Files:**
- Create: `ai-service/app/repositories/__init__.py`、`incident_repo.py`、`run_repo.py`
- Create: `ai-service/app/services/__init__.py`、`baseline_service.py`
- Test: `ai-service/tests/test_incident_repo.py`

**Interfaces:**
- Produces: `incident_repo.create_incident(title, description, severity, service_ref, observed_at) -> Incident`;`baseline_service.capture_digest_baseline(readonly_engine) -> dict`(从 `performance_schema.events_statements_summary_by_digest` 采目标查询计数);`run_repo.create_run(incident_id) -> AgentRun`(thread_id 生成 `run-{uuid}`);`agent_run.incident_digest_baseline` 存基线。Task 2.5 的 `list_expensive_query_digests` 使用该基线。

- [ ] **Step 1: 写失败测试**

`tests/test_incident_repo.py`:

```python
from sqlalchemy import text
from app.db.engine import get_control_engine
from app.repositories.incident_repo import create_incident
from app.repositories.run_repo import create_run
from app.services.baseline_service import capture_digest_baseline
from app.db.engine import get_readonly_engine


def test_create_incident_and_run():
    incident = create_incident("test incident", "desc", "high", "inventory-service")
    assert incident.id is not None
    run = create_run(incident.id)
    assert run.thread_id.startswith("run-")
    # 清理
    with get_control_engine().begin() as conn:
        conn.execute(text("DELETE FROM agent_run WHERE id=%s" % run.id))
        conn.execute(text("DELETE FROM incident WHERE id=%s" % incident.id))


def test_capture_digest_baseline_returns_dict():
    baseline = capture_digest_baseline(get_readonly_engine())
    assert isinstance(baseline, dict)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_incident_repo.py -v`
Expected: FAIL — 模块不存在。

- [ ] **Step 3: 写 repository 与 baseline_service**

`app/repositories/incident_repo.py`:

```python
from sqlalchemy.orm import Session
from app.db.engine import get_control_engine
from app.db.models import Incident


def create_incident(title: str, description: str, severity: str,
                    service_ref: str, observed_at=None) -> Incident:
    with Session(get_control_engine()) as session:
        inc = Incident(title=title, description=description, severity=severity,
                       service_ref=service_ref, observed_at=observed_at, status="created")
        session.add(inc)
        session.commit()
        session.refresh(inc)
        return inc
```

`app/repositories/run_repo.py`:

```python
import uuid
from sqlalchemy.orm import Session
from app.db.engine import get_control_engine
from app.db.models import AgentRun


def create_run(incident_id: int, baseline: dict | None = None) -> AgentRun:
    with Session(get_control_engine()) as session:
        run = AgentRun(incident_id=incident_id, thread_id=f"run-{uuid.uuid4()}",
                       status="created", incident_digest_baseline=baseline)
        session.add(run)
        session.commit()
        session.refresh(run)
        return run
```

`app/services/baseline_service.py`:

```python
from sqlalchemy import text, Engine

# 目标查询 digest 文本匹配(INVENTORY_LOOKUP 的规范化指纹前缀)
TARGET_DIGEST_LIKE = "%inventory%sku_id%warehouse_id%"


def capture_digest_baseline(engine: Engine) -> dict:
    """从 performance_schema 采目标查询的累计计数快照。"""
    sql = text("""
        SELECT DIGEST_TEXT, COUNT_STAR, SUM_TIMER_WAIT, SUM_ROWS_EXAMINED
        FROM performance_schema.events_statements_summary_by_digest
        WHERE DIGEST_TEXT LIKE :pattern
    """)
    baseline: dict[str, dict] = {}
    with engine.connect() as conn:
        for row in conn.execute(sql, {"pattern": TARGET_DIGEST_LIKE}):
            baseline[row.DIGEST_TEXT] = {
                "count": int(row.COUNT_STAR),
                "total_latency_us": int(row.SUM_TIMER_WAIT) // 1000,
                "rows_examined": int(row.SUM_ROWS_EXAMINED),
            }
    return baseline
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_incident_repo.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/repositories/ ai-service/app/services/ ai-service/tests/test_incident_repo.py
git commit -m "feat(ai): Incident/AgentRun repository + digest 基线采集"
```

---

### Task 2.5: 七个受控工具(注册表 + 统一外壳 + 审计)

**Files:**
- Create: `ai-service/app/tools/__init__.py`、`schemas.py`、`registry.py`、`execute.py`
- Create: `ai-service/app/services/java_client.py`、`metrics_service.py`、`trace_service.py`、`slow_query_service.py`、`query_plan_service.py`、`index_info_service.py`、`fix_service.py`、`recovery_service.py`
- Create: `ai-service/app/repositories/tool_repo.py`、`event_repo.py`
- Test: `ai-service/tests/test_tools.py`

**Interfaces:**
- Produces: 工具注册表 `TOOL_REGISTRY: dict[str, ToolSpec]`,`ToolSpec(name, input_schema, fn)`;`execute_tool(tool_name, incident_id, **kwargs) -> dict`(统一外壳:计时、成功/失败、写 `tool_call` + `incident_event`);入参/出参模型见 `schemas.py`。五个调查工具完整实现,`execute_fix`/`verify_recovery` 为骨架(M3 接状态机)。Task 2.6/2.7 的 API 与验收依赖 `execute_tool`。

- [ ] **Step 1: 写失败测试(先测 metrics 与 query_plan)**

`tests/test_tools.py`(核心断言,实施时补全七工具):

```python
from app.tools.execute import execute_tool


def test_get_service_metrics_ok(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        class R:
            status_code = 200
            def json(self):
                return {"service": "inventory-service", "p95Ms": 12, "qps": 1.0,
                        "errorRate": 0.0, "representativeSlowTraceId": "t1"}
        return R()
    monkeypatch.setattr("app.services.java_client.httpx.get", fake_get)
    out = execute_tool("get_service_metrics", incident_id=None,
                       service_ref="inventory-service", window_seconds=300)
    assert out["success"] is True
    assert out["data"]["p95Ms"] == 12


def test_get_query_plan_rejects_unknown_ref():
    out = execute_tool("get_query_plan", incident_id=None,
                       query_ref="DROP_TABLES", sample_parameters={"skuId": 1})
    assert out["success"] is False
    assert out["error_code"] == "UNKNOWN_QUERY_REF"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_tools.py -v`
Expected: FAIL — 模块不存在。

- [ ] **Step 3: 写 schemas.py(统一外壳与白名单)**

`app/tools/schemas.py`:

```python
from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ToolResult(BaseModel):
    tool_call_id: str = ""
    success: bool
    observed_at: str = Field(default_factory=utcnow_iso)
    duration_ms: int = 0
    data: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None


SERVICE_REF_WHITELIST = {"order-service", "inventory-service"}
TABLE_REF_WHITELIST = {"inventory"}
QUERY_REF_WHITELIST = {"INVENTORY_LOOKUP"}


class GetServiceMetricsIn(BaseModel):
    service_ref: str = Field(pattern="^(order-service|inventory-service)$")
    window_seconds: int = Field(ge=10, le=3600)


class GetTraceIn(BaseModel):
    trace_id: str = Field(min_length=1, max_length=64)


class ListDigestsIn(BaseModel):
    incident_id: int = Field(gt=0)


class GetQueryPlanIn(BaseModel):
    query_ref: str = Field(pattern="^(INVENTORY_LOOKUP)$")
    sample_parameters: dict[str, int]  # 仅整数参数,白名单模板内格式化


class GetIndexInfoIn(BaseModel):
    table_ref: str = Field(pattern="^(inventory)$")


class ExecuteFixIn(BaseModel):
    incident_id: int = Field(gt=0)
    fix_proposal_id: int = Field(gt=0)
    approval_id: int = Field(gt=0)


class VerifyRecoveryIn(BaseModel):
    incident_id: int = Field(gt=0)
    fix_execution_id: int = Field(gt=0)
```

- [ ] **Step 4: 写五个调查工具服务**

`app/services/java_client.py`:

```python
import httpx
from app.config import settings


def get_metrics(service: str, window_seconds: int) -> dict:
    resp = httpx.get(f"{settings.inventory_service_url}/internal/observations/metrics",
                     params={"window_seconds": window_seconds}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_trace_records(service_url: str, trace_id: str) -> list[dict] | None:
    resp = httpx.get(f"{service_url}/internal/observations/traces/{trace_id}", timeout=10)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()
```

`app/services/query_plan_service.py`(白名单 + 参数化 EXPLAIN):

```python
from sqlalchemy import text
from app.db.engine import get_readonly_engine
from app.tools.schemas import QUERY_REF_WHITELIST

# 服务端固化 SQL 模板,LLM 永远无法提交完整 SQL
QUERY_REGISTRY = {
    "INVENTORY_LOOKUP": "SELECT id, sku_id, warehouse_id, quantity FROM inventory "
                        "WHERE sku_id = {skuId} AND warehouse_id = {warehouseId}",
}


def explain(query_ref: str, sample_parameters: dict) -> dict:
    if query_ref not in QUERY_REF_WHITELIST:
        raise ValueError("UNKNOWN_QUERY_REF")
    sql = QUERY_REGISTRY[query_ref].format(**sample_parameters)  # 参数经 Pydantic 校验为 int
    explain_sql = f"EXPLAIN FORMAT=JSON {sql}"
    with get_readonly_engine().connect() as conn:
        row = conn.execute(text(explain_sql)).fetchone()
        return row[0] if isinstance(row[0], dict) else {"explain": row[0]}
```

`app/services/slow_query_service.py`(基线差值):

```python
from sqlalchemy import text
from app.db.engine import get_control_engine, get_readonly_engine
from app.services.baseline_service import TARGET_DIGEST_LIKE


def list_expensive_digests(incident_id: int) -> list[dict]:
    with get_control_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT incident_digest_baseline FROM agent_run "
            "WHERE incident_id = :i ORDER BY id DESC LIMIT 1"), {"i": incident_id}).fetchone()
        baseline = row[0] if row and row[0] else {}

    current: dict[str, dict] = {}
    with get_readonly_engine().connect() as conn:
        for r in conn.execute(text("""
            SELECT DIGEST_TEXT, COUNT_STAR, SUM_TIMER_WAIT, SUM_ROWS_EXAMINED
            FROM performance_schema.events_statements_summary_by_digest
            WHERE DIGEST_TEXT LIKE :p"""), {"p": TARGET_DIGEST_LIKE}):
            current[r.DIGEST_TEXT] = {"count": int(r.COUNT_STAR),
                                      "total_latency_us": int(r.SUM_TIMER_WAIT) // 1000,
                                      "rows_examined": int(r.SUM_ROWS_EXAMINED)}

    delta = []
    for digest, cur in current.items():
        base = baseline.get(digest, {"count": 0, "total_latency_us": 0, "rows_examined": 0})
        delta.append({
            "digest": digest[:200],
            "count_delta": cur["count"] - base["count"],
            "total_latency_us_delta": cur["total_latency_us"] - base["total_latency_us"],
            "rows_examined_delta": cur["rows_examined"] - base["rows_examined"],
        })
    delta.sort(key=lambda d: -d["rows_examined_delta"])
    return delta
```

`app/services/trace_service.py`、`index_info_service.py`、`metrics_service.py` 按同风格实现(见 Interfaces)。

- [ ] **Step 5: 写 registry.py 与 execute.py(统一外壳 + 审计)**

`app/tools/registry.py`:

```python
from typing import Any, Callable
from pydantic import BaseModel


class ToolSpec:
    def __init__(self, name: str, input_schema: type[BaseModel], fn: Callable[..., dict]):
        self.name = name
        self.input_schema = input_schema
        self.fn = fn


TOOL_REGISTRY: dict[str, ToolSpec] = {}
```

`app/tools/execute.py`:

```python
import time
import uuid
from typing import Any

from app.repositories.tool_repo import record_tool_call
from app.repositories.event_repo import append_event
from app.tools.schemas import ToolResult
from app.tools import registry


def execute_tool(tool_name: str, incident_id: int | None, **kwargs) -> dict:
    spec = registry.TOOL_REGISTRY[tool_name]
    parsed = spec.input_schema(**kwargs)  # Pydantic 校验失败会抛 422 语义错误
    start = time.monotonic()
    try:
        data = spec.fn(**parsed.model_dump())
        result = ToolResult(tool_call_id=str(uuid.uuid4())[:8], success=True,
                            duration_ms=int((time.monotonic() - start) * 1000), data=data)
    except Exception as e:  # noqa: BLE001
        result = ToolResult(tool_call_id=str(uuid.uuid4())[:8], success=False,
                            duration_ms=int((time.monotonic() - start) * 1000),
                            error_code=str(e) if isinstance(e, ValueError) else "TOOL_ERROR",
                            error_message=str(e))
    if incident_id is not None:
        record_tool_call(incident_id, tool_name, kwargs, result.model_dump())
        append_event(incident_id, "tool_call", {"tool": tool_name, "result": result.model_dump()})
    return result.model_dump()
```

- [ ] **Step 6: 注册七工具(在 app/main.py 或 tools/__init__.py 中 import 并注册)**

```python
# app/tools/__init__.py 末尾
from app.tools.registry import TOOL_REGISTRY, ToolSpec
from app.tools.schemas import (GetServiceMetricsIn, GetTraceIn, ListDigestsIn,
                               GetQueryPlanIn, GetIndexInfoIn, ExecuteFixIn, VerifyRecoveryIn)
from app.services import (metrics_service, trace_service, slow_query_service,
                          query_plan_service, index_info_service, fix_service, recovery_service)

TOOL_REGISTRY.update({
    "get_service_metrics": ToolSpec("get_service_metrics", GetServiceMetricsIn, metrics_service.get_metrics),
    "get_trace": ToolSpec("get_trace", GetTraceIn, trace_service.get_trace),
    "list_expensive_query_digests": ToolSpec("list_expensive_query_digests", ListDigestsIn, slow_query_service.list_expensive_digests),
    "get_query_plan": ToolSpec("get_query_plan", GetQueryPlanIn, query_plan_service.explain),
    "get_index_info": ToolSpec("get_index_info", GetIndexInfoIn, index_info_service.get_index_info),
    "execute_fix": ToolSpec("execute_fix", ExecuteFixIn, fix_service.execute_fix),
    "verify_recovery": ToolSpec("verify_recovery", VerifyRecoveryIn, recovery_service.verify_recovery),
})
```

- [ ] **Step 7: 运行测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_tools.py -v`
Expected: PASS。

- [ ] **Step 8: 提交**

```bash
git add ai-service/app/tools/ ai-service/app/services/ ai-service/app/repositories/ ai-service/tests/test_tools.py
git commit -m "feat(ai): 七受控工具注册表 + 统一外壳 + 审计"
```

---

### Task 2.6: 基础 API 与演示场景代理

**Files:**
- Create: `ai-service/app/api/__init__.py`、`incidents.py`、`runs.py`、`demo.py`
- Modify: `ai-service/app/main.py`(挂路由)
- Test: `ai-service/tests/test_api_incidents.py`

**Interfaces:**
- Produces: `POST /api/incidents`(创建 + 采 digest 基线 → 201,body `{title, description, severity, service_ref, observed_at?}`);`GET /api/incidents`;`GET /api/incidents/{incident_id}`;`POST /api/incidents/{incident_id}/investigations`(创建 AgentRun → 202,body 返回 run;M3 启动 Agent);`GET /api/incidents/{incident_id}/runs/{run_id}`;`POST /api/demo/scenarios/SCN-001/{inject|reset}`、`GET .../status`(仅 `demo_mode=true`,代理到 Java 带 `x-demo-key`)。

- [ ] **Step 1: 写失败测试**

`tests/test_api_incidents.py`:

```python
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_create_incident():
    resp = client.post("/api/incidents", json={
        "title": "库存查询变慢", "description": "P95 升高", "severity": "high",
        "service_ref": "inventory-service",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] is not None
    assert body["status"] == "created"


def test_start_investigation_returns_202():
    inc = client.post("/api/incidents", json={"title": "t", "severity": "medium",
                                              "service_ref": "inventory-service"}).json()
    resp = client.post(f"/api/incidents/{inc['id']}/investigations")
    assert resp.status_code == 202
    assert resp.json()["run_id"] is not None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_api_incidents.py -v`
Expected: FAIL — 路由不存在(404)。

- [ ] **Step 3: 写 incidents.py / runs.py / demo.py 并挂路由**

`app/api/incidents.py`(节选):

```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.repositories import incident_repo
from app.services.baseline_service import capture_digest_baseline
from app.db.engine import get_readonly_engine

router = APIRouter(prefix="/api/incidents")


class IncidentIn(BaseModel):
    title: str
    description: str | None = None
    severity: str = "medium"
    service_ref: str
    observed_at: str | None = None


@router.post("", status_code=201)
def create_incident(payload: IncidentIn):
    inc = incident_repo.create_incident(
        payload.title, payload.description, payload.severity, payload.service_ref)
    baseline = capture_digest_baseline(get_readonly_engine())
    incident_repo.save_incident_baseline(inc.id, baseline)
    return {"id": inc.id, "status": inc.status, "title": inc.title}
```

`app/api/demo.py`(代理,`x-demo-key` 从配置取):

```python
import httpx
from fastapi import APIRouter, HTTPException
from app.config import settings
from app.db.engine import get_control_engine

router = APIRouter(prefix="/api/demo/scenarios/SCN-001")


def _proxy(action: str, method: str):
    if not settings.demo_mode:
        raise HTTPException(403, "DEMO_MODE disabled")
    resp = httpx.request(method, f"{settings.inventory_service_url}/internal/scenarios/SCN-001/{action}",
                         headers={"x-demo-key": settings.demo_key}, timeout=10)
    return resp.json(), resp.status_code


@router.post("/inject")
def inject():
    body, code = _proxy("inject", "POST")
    return body


@router.post("/reset")
def reset():
    body, code = _proxy("reset", "POST")
    return body


@router.get("/status")
def status():
    body, code = _proxy("status", "GET")
    return body
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_api_incidents.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/api/ ai-service/app/main.py ai-service/tests/test_api_incidents.py
git commit -m "feat(ai): Incident/AgentRun/演示场景 API"
```

---

### Task 2.7: M2 验收(不调 LLM 取齐 E1~E5)

**Files:**
- Create: `scripts/verify-m2.py`
- Test: 手动执行(见 Step 2)

**Interfaces:**
- Consumes: M1 全部(Java 服务 + SCN-001)、M2 全部(工具/API)。
- Produces: 验收脚本:重置场景 → 注入故障 → 创建 Incident → 依次执行五个调查工具 → 打印 E1~E5 证据,断言各证据存在。

- [ ] **Step 1: 写 verify-m2.py**

```python
"""M2 验收:不调用 LLM,通过七工具手动取齐 E1~E5 证据。
前置:两个 Java 服务已启动(inventory 需 DEMO_MODE=true);AI 服务已启动。
用法: python scripts/verify-m2.py
"""
import os
import sys
import httpx

AI = os.environ.get("AI_SERVICE_URL", "http://localhost:8000")
INV = os.environ.get("INVENTORY_SERVICE_URL", "http://localhost:8082")
DEMO_KEY = os.environ.get("DEMO_KEY", "demo-secret-2026")
SKU, WH = 42, 7


def main() -> int:
    c = httpx.Client(base_url=AI, timeout=15)
    # 重置并注入故障
    c.post("/api/demo/scenarios/SCN-001/reset", headers={"x-demo-key": DEMO_KEY})
    c.post("/api/demo/scenarios/SCN-001/inject", headers={"x-demo-key": DEMO_KEY})
    # 创建 Incident(基线)
    inc = c.post("/api/incidents", json={
        "title": "M2 验收", "severity": "high", "service_ref": "inventory-service"}).json()
    iid = inc["id"]
    run = c.post(f"/api/incidents/{iid}/investigations").json()
    rid = run["run_id"]
    # 工具调用(E1~E5)
    e1 = c.post(f"/api/incidents/{iid}/tools", json={"tool": "get_service_metrics",
                "args": {"service_ref": "inventory-service", "window_seconds": 300}}).json()
    trace_id = e1["data"]["representativeSlowTraceId"]
    e2 = c.post(f"/api/incidents/{iid}/tools", json={"tool": "get_trace",
                "args": {"trace_id": trace_id}}).json()
    e3 = c.post(f"/api/incidents/{iid}/tools", json={"tool": "list_expensive_query_digests",
                "args": {"incident_id": iid}}).json()
    e4 = c.post(f"/api/incidents/{iid}/tools", json={"tool": "get_query_plan",
                "args": {"query_ref": "INVENTORY_LOOKUP", "sample_parameters": {"skuId": SKU, "warehouseId": WH}}}).json()
    e5 = c.post(f"/api/incidents/{iid}/tools", json={"tool": "get_index_info",
                "args": {"table_ref": "inventory"}}).json()
    print("E1 metrics:", e1["success"], e1["data"])
    print("E2 trace:", e2["success"])
    print("E3 digests:", e3["success"], e3["data"])
    print("E4 explain:", e4["success"])
    print("E5 index_info:", e5["success"], e5["data"])
    ok = all(x["success"] for x in (e1, e2, e3, e4, e5))
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1
```

> 需在 API 中补充 `POST /api/incidents/{id}/tools`(统一工具调用入口,走 `execute_tool`;LLM_MODE=fake 时该端点即演示入口)。

- [ ] **Step 2: 执行验收**

前置:Java 双服务 + AI 服务启动。Run: `python scripts/verify-m2.py`
Expected: 输出 E1~E5 各工具 success=true,末尾 `RESULT: PASS`;E4 的 EXPLAIN 显示全表扫描、E5 确认索引缺失。

- [ ] **Step 3: 提交**

```bash
git add scripts/verify-m2.py
git commit -m "test(ai): M2 验收脚本(不调 LLM 取齐 E1~E5)"
```

---

## 后续里程碑

- **M3 LangGraph 闭环**(依赖 M2):九节点图、调查预算、E1~E5 根因闸门、恢复规则、AsyncSqliteSaver、审批 interrupt/恢复、过期审批扫描、幂等修复(no_op)、复盘报告。验收:纯 API 完成闭环。
- **M4 Vue 工作台**、**M5 最终交付**(Testcontainers/Dockerfile/Compose/E2E)。
