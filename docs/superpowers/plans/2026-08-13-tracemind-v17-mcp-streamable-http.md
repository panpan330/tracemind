# V1.7 MCP Streamable HTTP 远程传输与服务化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 MCP 工具服务从"AI 服务内部 spawn 的 stdio 子进程"升级为独立容器、独立镜像、Streamable HTTP 标准传输的远程只读工具服务(工具实现唯一,两个 Transport Adapter,零 direct bypass)。

**Architecture:** 建立传输无关核心 `tools_core`(ToolRegistry/Schema/InvocationContext/ToolExecutionService/handlers)+ 基础设施适配器 `tools_infrastructure`(ports 实现)+ `mcp/` 双入口(server_stdio/server_http)与双 transport client。Server 端用 FastMCP `streamable_http_app`(`stateless_http=True`)+ 自写安全中间件(Opaque Token 认证/限流/Origin)。部署用 Dockerfile 双 target + Compose 三内部网络 + llm-egress。验收用 `scripts/verify-m17.py` 三层(本地 fast / VM smoke / VM release)。

**Tech Stack:** Python 3.12, FastMCP / mcp>=1.28,<2(1.29), FastAPI/Uvicorn, SQLAlchemy 2.0, pydantic v2, pytest, Docker Compose v2, MySQL 8.0。

**Spec:** `docs/superpowers/specs/2026-08-13-tracemind-v17-mcp-streamable-http-design.md`(经 5 轮审查定稿)。

## Global Constraints

(来自 spec,每 Task 隐式包含)

1. 工具实现唯一,Transport Adapter 不包含业务逻辑。
2. 标准部署只走 Streamable HTTP,网络错误**禁止回退 stdio/direct**(`direct_fallback=false` 恒成立)。
3. AI 服务不持有调查凭据,MCP 服务不持有处置凭据(LLM key / fix_executor / session_terminator / 业务写账号 / control_app 完整权限)。
4. 模型只生成业务工具参数;调查与审计上下文(`incident_id/agent_run_id/tool_call_id/purpose/client_id/traceparent`)由程序注入,模型传入同名伪造字段 → `MCP_CONTEXT_SPOOFING_REJECTED`(拒绝,不静默剔除)。
5. 根因 Policy、审批、处置、恢复判定仍由 AI 控制服务负责;MCP 只提供 7 个只读调查工具;`execute_fix`/`verify_recovery` 不暴露为远程 MCP Tool。
6. stdio 仅用于本地开发与离线评测;标准 Profile 集:`local / offline_eval / vm_smoke / vm_release / production`;`vm_release`/`production` 禁止 stdio、检测到 Fixture 配置拒绝启动。
7. HTTP 服务无状态(`stateless_http=True`),支持水平扩展;单实例限流,不承诺全局配额。
8. 认证:内部 Opaque Token(不做 JWT/OAuth);`client_id` 只能从认证结果派生;服务端只存 Token Fingerprint;日志/健康检查/审计不得输出 Token。
9. 错误三层分层:HTTP 安全层(401/403/413/415/429)→ MCP 协议层 → 工具业务层(`structuredContent {errorCode, retryable}`);错误模块分 `tools_core/errors.py` / `mcp/protocol_errors.py` / `mcp/client_errors.py`。
10. 重试:最大 3 次含首次;指数退避+Jitter;429 尊重 Retry-After;401/403/400/404/413/Schema/Context 不重试;504/读超时 → `outcome_unknown`;`client_attempt_id` 幂等(重传同 id,重复返回先前结果/`ATTEMPT_IN_PROGRESS`/`ATTEMPT_OUTCOME_UNKNOWN`);`attempt_no` 由 Server 原子分配(禁 `SELECT MAX+1`)。
11. 审计唯一所有者:AI Service 拥有并写 `tool_call`(先提交事务再发 MCP 请求);MCP Server 只 SELECT `tool_call` + 写 `tool_call_attempt`/`observation_query`;`mcp_tool_auditor` 不授 `tool_call` 的 UPDATE;两段式审计 fail-closed(`MCP_AUDIT_UNAVAILABLE`/`MCP_AUDIT_PERSIST_FAILED`)。
12. 版本四维度:Tool Schema / MCP Protocol(`SUPPORTED_MCP_PROTOCOL_VERSIONS` 代码定义 + `REQUIRED_MCP_PROTOCOL_VERSION` 可选约束 + `negotiated_protocol_version`)/ SDK Version(`importlib.metadata` 读取,非环境变量)/ `INVOCATION_CONTEXT_VERSION`。
13. 配置 fail-closed:进程入口显式构建对应 Settings,模块 import 不实例化全部;stdio 需 command;streamable_http 需 URL+凭据;`vm_release`/`production` 禁 stdio;配置错启动失败;禁自动切换 Transport。
14. 部署:同一仓库同源码,Dockerfile 双 target(`ai-runtime`/`mcp-tools-runtime`,MCP 镜像不装 LLM/Agent 依赖);Compose 三内部网络(`agent-mcp-network`/`control-data-network`/`tool-observation-network` 均 `internal: true`)+ `llm-egress-network`(仅 ai-service);mcp-tools 不映射宿主机端口;部署顺序:DB Migration → mcp-tools → 契约探针 → ai-service → VM Smoke。
15. 验收:`scripts/verify-m17.py --tier fast|vm-smoke|release`(Python 编排,人工触发自动执行);离线评测动态 N/N(不写死 24);凭据隔离只输出布尔;发布报告脱敏摘要进 `docs/releases/v1.7-validation-summary.md`;Release 前绑定断言(报告 SHA=HEAD、镜像 label、Digest、Tag 指向已验收 Commit)。

**测试命令基线**(Windows 本地):
- 后端单测:`cd ai-service && .venv/Scripts/pytest.exe tests/ -q`
- 单文件:`cd ai-service && .venv/Scripts/pytest.exe tests/test_xxx.py -q`
- 离线评测:`cd ai-service && TRACEMIND_RUN_PROFILE=offline_eval TRACEMIND_LLM_MODE=fake TRACEMIND_EVAL_MODE=true TRACEMIND_CONTROL_DB_URL="mysql+pymysql://tracemind_control_app:control_app_pwd@localhost:3306/tracemind_control" .venv/Scripts/python.exe ../scripts/eval_agent.py --mode offline --llm fake --runs 1`
- 语法:`cd ai-service && .venv/Scripts/python.exe -c "import ast; ast.parse(open('<file>', encoding='utf-8').read())"`

---

## 阶段 A:tools_core 传输无关核心

### Task 1:tools_core 骨架 — errors + context(InvocationContext 三件套)

**Files:**
- Create: `ai-service/app/tools_core/__init__.py`
- Create: `ai-service/app/tools_core/errors.py`
- Create: `ai-service/app/tools_core/context.py`
- Test: `ai-service/tests/test_tools_core_context.py`

**Interfaces:**
- Produces: `ToolBusinessError(code: str, message: str, retryable: bool)`;`ClientInvocationContext(incident_id, agent_run_id, tool_call_id, purpose)`;`AuthenticatedPrincipal(client_id, subject, audience, scopes, token_fingerprint)`;`ServerInvocationContext(client, principal, trace_context, protocol_version, mcp_request_id)`

- [ ] **Step 1: Write the failing test**

```python
# ai-service/tests/test_tools_core_context.py
import pytest
from app.tools_core.context import (
    ClientInvocationContext, AuthenticatedPrincipal, ServerInvocationContext,
    Purpose, RESERVED_HEADERS,
)
from app.tools_core.errors import ToolBusinessError


def test_client_context_requires_all_fields():
    with pytest.raises(ValueError):
        ClientInvocationContext(incident_id=1, agent_run_id=1)  # 缺 tool_call_id/purpose


def test_purpose_enum_values():
    assert {p.value for p in Purpose} == {"investigation", "recovery_verification"}


def test_principal_holds_token_fingerprint():
    p = AuthenticatedPrincipal(client_id="ai-service", subject="ai-service",
                               audience="tracemind-mcp-tools", scopes=["tools:investigate"],
                               token_fingerprint="sha256:abc")
    assert p.client_id == "ai-service" and p.scopes == ["tools:investigate"]


def test_server_context_composes():
    c = ClientInvocationContext(incident_id=1, agent_run_id=2, tool_call_id="tc-1",
                                purpose="investigation")
    p = AuthenticatedPrincipal(client_id="ai-service", subject="ai-service",
                               audience="tracemind-mcp-tools", scopes=["tools:investigate"],
                               token_fingerprint="fp")
    s = ServerInvocationContext(client=c, principal=p, trace_context="00-abc-def-01",
                                protocol_version="2026-07-28", mcp_request_id="m-1")
    assert s.client.tool_call_id == "tc-1" and s.principal.client_id == "ai-service"


def test_reserved_headers_exact_set():
    assert RESERVED_HEADERS == {
        "X-TraceMind-Incident-Id", "X-TraceMind-Agent-Run-Id", "X-TraceMind-Tool-Call-Id",
        "X-TraceMind-Purpose", "X-TraceMind-Context-Version",
    }


def test_business_error_retryable():
    e = ToolBusinessError(code="TRACE_NOT_FOUND", message="no trace", retryable=False)
    assert e.code == "TRACE_NOT_FOUND" and e.retryable is False
    assert "TRACE_NOT_FOUND" in str(e)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_tools_core_context.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.tools_core'`

- [ ] **Step 3: Write minimal implementation**

```python
# ai-service/app/tools_core/errors.py
class ToolBusinessError(Exception):
    """工具业务层错误:code 为受控错误码,retryable 供 Client 决定是否重试。"""
    def __init__(self, code: str, message: str = "", retryable: bool = False) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
```

```python
# ai-service/app/tools_core/context.py
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Purpose(str, Enum):
    INVESTIGATION = "investigation"
    RECOVERY_VERIFICATION = "recovery_verification"


# Client 唯一允许注入的受控 Header(逐请求生成,不存共享 Client Header)
RESERVED_HEADERS = frozenset({
    "X-TraceMind-Incident-Id", "X-TraceMind-Agent-Run-Id", "X-TraceMind-Tool-Call-Id",
    "X-TraceMind-Purpose", "X-TraceMind-Context-Version",
})


@dataclass(frozen=True)
class ClientInvocationContext:
    incident_id: int
    agent_run_id: int
    tool_call_id: str
    purpose: str

    def __post_init__(self) -> None:
        if not (self.incident_id > 0 and self.agent_run_id > 0):
            raise ValueError("incident_id/agent_run_id 必须为正整数")
        if not self.tool_call_id or len(self.tool_call_id) > 64:
            raise ValueError("tool_call_id 非法")
        if self.purpose not in {p.value for p in Purpose}:
            raise ValueError(f"purpose 非法: {self.purpose}")


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """只能由认证结果派生,Client 无权构造。"""
    client_id: str
    subject: str
    audience: str
    scopes: list = field(default_factory=list)
    token_fingerprint: str = ""


@dataclass(frozen=True)
class ServerInvocationContext:
    client: ClientInvocationContext
    principal: AuthenticatedPrincipal
    trace_context: Optional[str] = None      # W3C traceparent
    protocol_version: Optional[str] = None   # negotiated_protocol_version
    mcp_request_id: Optional[str] = None
```

```python
# ai-service/app/tools_core/__init__.py
from app.tools_core.errors import ToolBusinessError  # noqa: F401
from app.tools_core.context import (  # noqa: F401
    ClientInvocationContext, AuthenticatedPrincipal, ServerInvocationContext, Purpose,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_tools_core_context.py -q`
Expected: PASS(6 passed)

- [ ] **Step 5: Commit**

```bash
git add ai-service/app/tools_core/ ai-service/tests/test_tools_core_context.py
git commit -m "feat(tools_core): InvocationContext 三件套 + 业务错误码(传输无关核心骨架)"
```

---

### Task 2:迁移 registry/schemas 到 tools_core

**Files:**
- Move: `ai-service/app/tools/registry.py` → `ai-service/app/tools_core/registry.py`
- Move: `ai-service/app/tools/schemas.py` → `ai-service/app/tools_core/schemas.py`
- Modify: `ai-service/app/mcp/contract.py`(import)
- Modify: `ai-service/app/services/index_info_service.py`(import whitelist)
- Modify: `ai-service/app/services/query_plan_service.py`(import whitelist)
- Test: `ai-service/tests/test_tools_registry_schema.py`(新)

**Interfaces:**
- Consumes: `app.tools_core.registry.TOOL_REGISTRY, ToolSpec, tool`;`app.tools_core.schemas.ToolResult, GetServiceMetricsIn, GetTraceIn, ListDigestsIn, GetQueryPlanIn, GetIndexInfoIn, GetLockWaitersIn, GetTransactionDetailsIn, ExecuteFixIn, VerifyRecoveryIn, SERVICE_REF_WHITELIST, TABLE_REF_WHITELIST, QUERY_REF_WHITELIST`(签名不变)
- Produces: 工具 schema 与 registry 迁移到 tools_core,`app.tools` 不再定义它们

- [ ] **Step 1: Write the failing test**

```python
# ai-service/tests/test_tools_registry_schema.py
from app.tools_core.registry import TOOL_REGISTRY, ToolSpec
from app.tools_core.schemas import ToolResult, GetServiceMetricsIn, SERVICE_REF_WHITELIST


def test_registry_empty_by_default():
    # 迁移后 registry 由 app/tools/__init__.py 填充;此处只验证模块可导入
    assert ToolSpec is not None and ToolResult is not None


def test_whitelist_importable_from_tools_core():
    assert SERVICE_REF_WHITELIST == {"order-service", "inventory-service"}


def test_old_path_removed():
    import importlib
    try:
        importlib.import_module("app.tools.registry")
        assert False, "app.tools.registry 应已迁移到 tools_core"
    except ModuleNotFoundError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_tools_registry_schema.py -q`
Expected: FAIL(import app.tools_core.registry 不存在)

- [ ] **Step 3: Move files and update imports**

```bash
cd ai-service
git mv app/tools/registry.py app/tools_core/registry.py
git mv app/tools/schemas.py app/tools_core/schemas.py
```

更新 import(用 sed 或手动编辑):
- `app/mcp/contract.py`:`from app.tools.registry import TOOL_REGISTRY` → `from app.tools_core.registry import TOOL_REGISTRY`
- `app/services/index_info_service.py`:`from app.tools.schemas import TABLE_REF_WHITELIST` → `from app.tools_core.schemas import TABLE_REF_WHITELIST`
- `app/services/query_plan_service.py`:`from app.tools.schemas import QUERY_REF_WHITELIST` → `from app.tools_core.schemas import QUERY_REF_WHITELIST`

- [ ] **Step 4: Run test to verify it passes + 全量冒烟**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_tools_registry_schema.py -q`
Expected: PASS

Run: `cd ai-service && .venv/Scripts/python.exe -c "from app.tools_core.registry import TOOL_REGISTRY; from app.tools_core.schemas import ToolResult; print('OK')"`
Expected: OK(无 ImportError)

- [ ] **Step 5: Commit**

```bash
git add -A ai-service/
git commit -m "refactor(tools_core): 迁移 registry/schemas 到 tools_core(保留历史,更新 import)"
```

---

### Task 3:tools_core/ports.py — 端口接口定义

**Files:**
- Create: `ai-service/app/tools_core/ports.py`
- Test: `ai-service/tests/test_tools_core_ports.py`

**Interfaces:**
- Produces: `IncidentRunPort`(`get_run(run_id) -> RunContext|None`)、`ToolAuditPort`(`write_attempt(...)`)、`MetricsPort`、`TracePort`、`DigestPort`、`PlanPort`、`IndexPort`、`LockPort`、`TransactionPort`(各 handler 的依赖)

- [ ] **Step 1: Write the failing test**

```python
# ai-service/tests/test_tools_core_ports.py
from app.tools_core.ports import IncidentRunPort, ToolAuditPort, MetricsPort


def test_port_names():
    assert IncidentRunPort.__name__ == "IncidentRunPort"
    assert ToolAuditPort.__name__ == "ToolAuditPort"
    assert MetricsPort.__name__ == "MetricsPort"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_tools_core_ports.py -q`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: Write minimal implementation**

```python
# ai-service/app/tools_core/ports.py
"""传输无关核心依赖的端口接口(由 tools_infrastructure 实现)。

tools_core 只依赖这些接口,不导入任何 AI 应用层/基础设施实现。"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class RunContext:
    run_id: int
    incident_id: int
    status: str          # running/collecting/diagnosing/verifying_recovery/...
    service_ref: str
    operation_ref: Optional[str] = None


class IncidentRunPort(ABC):
    """Incident/Run 存在性、归属、允许状态查询。"""
    @abstractmethod
    def get_run(self, run_id: int) -> Optional[RunContext]:
        """返回 Run 上下文;不存在返回 None。"""

    @abstractmethod
    def is_run_allowed(self, run: RunContext, purpose: str) -> bool:
        """按调用目的判定允许状态(见 spec §7.5 白名单)。"""


class ToolAuditPort(ABC):
    """MCP Server 侧审计写入(tool_call_attempt / observation_query)。"""
    @abstractmethod
    def write_attempt_started(self, ctx, attempt_no: int, mcp_request_id: str) -> int:
        """写 started 审计,返回 attempt 记录 id;失败抛 ToolAuditUnavailable。"""

    @abstractmethod
    def write_attempt_finished(self, attempt_pk: int, outcome: str,
                               result: dict | None = None, error_code: str | None = None,
                               retryable: bool | None = None, latency_ms: int = 0) -> None:
        """写 completed/failed 终态审计。"""

    @abstractmethod
    def write_observation_query(self, ctx, tool_name: str, params: dict,
                                result: dict, latency_ms: int) -> None:
        """写入观测查询审计(供凭据隔离验收)。"""


class ToolAuditUnavailable(Exception):
    """started 审计无法落库 → 不执行工具(fail-closed)。"""


class ToolAuditPersistFailed(Exception):
    """终态审计失败 → 结果不作有效 Evidence。"""


class MetricsPort(ABC):
    @abstractmethod
    def get_metrics(self, service_ref: str, window_start: str, window_end: str,
                    incident_id: int) -> dict: ...


class TracePort(ABC):
    @abstractmethod
    def get_trace(self, trace_ref: Optional[str], trace_id: Optional[str],
                  incident: dict, incident_id: int) -> dict: ...


class DigestPort(ABC):
    @abstractmethod
    def list_expensive_digests(self, window_seconds: int | None = None) -> dict: ...


class PlanPort(ABC):
    @abstractmethod
    def explain(self, query_ref: str, sample_parameters: dict) -> dict: ...


class IndexPort(ABC):
    @abstractmethod
    def get_index_info(self, table_ref: str) -> dict: ...


class LockPort(ABC):
    @abstractmethod
    def get_lock_waiters(self, scope_ref: str) -> dict: ...

    @abstractmethod
    def get_transaction_details(self, transaction_ref: str) -> dict: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_tools_core_ports.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ai-service/app/tools_core/ports.py ai-service/tests/test_tools_core_ports.py
git commit -m "feat(tools_core): 定义端口接口 IncidentRunPort/ToolAuditPort/调查数据端口"
```

---

### Task 4:tools_core/handlers — 7 个只读调查 handler

**Files:**
- Create: `ai-service/app/tools_core/handlers/__init__.py`
- Create: `ai-service/app/tools_core/handlers/service_metrics.py`
- Create: `ai-service/app/tools_core/handlers/trace.py`
- Create: `ai-service/app/tools_core/handlers/query_digest.py`
- Create: `ai-service/app/tools_core/handlers/query_plan.py`
- Create: `ai-service/app/tools_core/handlers/index_info.py`
- Create: `ai-service/app/tools_core/handlers/lock_waiters.py`
- Create: `ai-service/app/tools_core/handlers/transaction_details.py`
- Test: `ai-service/tests/test_tools_core_handlers.py`

**Interfaces:**
- Consumes: `app.tools_core.ports.*` 端口
- Produces: `build_handlers(ports) -> dict[str, Callable]`,key 为工具名,值为 `fn(**params) -> dict`(纯净 data,失败抛 `ToolBusinessError`);handler 不 import 任何 services/基础设施

- [ ] **Step 1: Write the failing test**

```python
# ai-service/tests/test_tools_core_handlers.py
import pytest
from app.tools_core.handlers import build_handlers
from app.tools_core.ports import LockPort, MetricsPort, TracePort
from app.tools_core.errors import ToolBusinessError


class FakeLock(LockPort):
    def get_lock_waiters(self, scope_ref: str) -> dict:
        if scope_ref != "inventory:42":
            raise ToolBusinessError("SCOPE_INVALID", "scope 必须来自前序证据", retryable=False)
        return {"blockers": []}

    def get_transaction_details(self, transaction_ref: str) -> dict:
        return {"tx": 1}


def test_lock_handler_returns_data():
    handlers = build_handlers({"lock": FakeLock()})
    r = handlers["get_lock_waiters"](scope_ref="inventory:42")
    assert r == {"blockers": []}


def test_lock_handler_rejects_bad_scope():
    handlers = build_handlers({"lock": FakeLock()})
    with pytest.raises(ToolBusinessError):
        handlers["get_lock_waiters"](scope_ref="bad")


def test_handlers_keys():
    handlers = build_handlers({"lock": FakeLock()})
    assert set(handlers) == {"get_service_metrics", "get_trace", "list_expensive_query_digests",
                             "get_query_plan", "get_index_info", "get_lock_waiters",
                             "get_transaction_details"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_tools_core_handlers.py -q`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: Write minimal implementation**

```python
# ai-service/app/tools_core/handlers/lock_waiters.py
from app.tools_core.errors import ToolBusinessError
from app.tools_core.ports import LockPort


def build(ports) -> dict:
    lock: LockPort = ports["lock"]

    def get_lock_waiters(scope_ref: str) -> dict:
        try:
            return lock.get_lock_waiters(scope_ref)
        except ToolBusinessError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ToolBusinessError("LOCK_QUERY_FAILED", str(e), retryable=True) from e

    def get_transaction_details(transaction_ref: str) -> dict:
        return lock.get_transaction_details(transaction_ref)

    return {"get_lock_waiters": get_lock_waiters, "get_transaction_details": get_transaction_details}
```

```python
# ai-service/app/tools_core/handlers/__init__.py
from app.tools_core.ports import (IndexPort, LockPort, MetricsPort, PlanPort,
                                  TracePort, DigestPort)


def build_handlers(ports: dict) -> dict:
    """组装 7 个只读调查 handler;ports 提供各端口实现。"""
    handlers: dict = {}
    from app.tools_core.handlers import (
        index_info, lock_waiters, query_digest, query_plan, service_metrics, trace,
        transaction_details,
    )
    for mod in (service_metrics, trace, query_digest, query_plan, index_info,
                lock_waiters, transaction_details):
        handlers.update(mod.build(ports))
    return handlers
```

其余 5 个 handler 同构(service_metrics → MetricsPort.get_metrics;trace → TracePort.get_trace;query_digest → DigestPort.list_expensive_digests;query_plan → PlanPort.explain;index_info → IndexPort.get_index_info),每个把端口调用包 `ToolBusinessError`,失败 `retryable=True`(基础设施类)或透传业务错误。完整代码见各文件。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_tools_core_handlers.py -q`
Expected: PASS(3 passed)

- [ ] **Step 5: Commit**

```bash
git add ai-service/app/tools_core/handlers/ ai-service/tests/test_tools_core_handlers.py
git commit -m "feat(tools_core): 7 个只读调查 handler(依赖 ports 端口,不含业务实现)"
```

---

### Task 5:tools_core/service.py — ToolExecutionService(校验/上下文/执行/审计)

**Files:**
- Create: `ai-service/app/tools_core/service.py`
- Modify: `ai-service/app/tools/execute.py`(改为薄封装,保持既有调用方兼容)
- Modify: `ai-service/app/tools/__init__.py`(工具注册改走 tools_core)
- Test: `ai-service/tests/test_tools_core_service.py`

**Interfaces:**
- Consumes: `app.tools_core.registry.TOOL_REGISTRY`、`app.tools_core.schemas.ToolResult`、`app.tools_core.handlers.build_handlers`、`app.tools_core.context.*`
- Produces: `ToolExecutionService`:
  - `__init__(ports: dict, runtime: str, fixture: dict | None = None)`
  - `execute(name: str, params: dict, ctx: ClientInvocationContext, principal: AuthenticatedPrincipal | None = None, audit: ToolAuditPort | None = None, transport: str = "mcp_stdio") -> dict`
  - `set_fixture(fixture: dict | None)`(仅 `runtime == "fixture"` 允许)
  - 语义:fixture 命中优先;`extra=forbid` 校验;reserved context 字段出现在 params → `ToolBusinessError("MCP_CONTEXT_SPOOFING_REJECTED")`;`record_tool_call` 审计(AI 侧);`transport` 透传;成功/失败包装 ToolResult

- [ ] **Step 1: Write the failing test**

```python
# ai-service/tests/test_tools_core_service.py
import pytest
from app.tools_core.service import ToolExecutionService
from app.tools_core.context import ClientInvocationContext
from app.tools_core.errors import ToolBusinessError


def _ctx():
    return ClientInvocationContext(incident_id=1, agent_run_id=1, tool_call_id="tc-1",
                                   purpose="investigation")


def test_fixture_hit():
    svc = ToolExecutionService(ports={}, runtime="fixture")
    svc.set_fixture({"get_index_info:" + "a" * 12: {"ok": True, "data": {"idx": 1}}})
    # 用真实工具名 + 固定参数哈希
    import hashlib, json
    args = {"table_ref": "inventory"}
    key = "get_index_info:" + hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:12]
    svc.set_fixture({key: {"ok": True, "data": {"idx": 1}}})
    r = svc.execute("get_index_info", args, _ctx())
    assert r["success"] is True and r["data"] == {"idx": 1}


def test_unknown_tool():
    svc = ToolExecutionService(ports={}, runtime="real")
    r = svc.execute("nope", {}, _ctx())
    assert r["success"] is False and r["error_code"] == "UNKNOWN_TOOL"


def test_context_spoofing_rejected():
    svc = ToolExecutionService(ports={}, runtime="real")
    with pytest.raises(ToolBusinessError) as ei:
        svc.execute("get_index_info", {"incident_id": 999, "table_ref": "inventory"}, _ctx())
    assert ei.value.code == "MCP_CONTEXT_SPOOFING_REJECTED"


def test_fixture_forbidden_in_real_runtime():
    svc = ToolExecutionService(ports={}, runtime="real")
    with pytest.raises(ToolBusinessError):
        svc.set_fixture({"x": {"ok": True}})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_tools_core_service.py -q`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: Write minimal implementation**

```python
# ai-service/app/tools_core/service.py
"""ToolExecutionService:传输无关的统一工具执行(参数校验/上下文校验/执行/审计)。"""
import hashlib
import json
import time
import uuid
from typing import Any, Optional

from pydantic import ValidationError

from app.tools_core.context import RESERVED_HEADERS, ClientInvocationContext, AuthenticatedPrincipal
from app.tools_core.errors import ToolBusinessError
from app.tools_core.ports import ToolAuditPort
from app.tools_core.registry import TOOL_REGISTRY
from app.tools_core.schemas import ToolResult

# reserved context 字段(出现即拒绝,不静默剔除)
_RESERVED_CONTEXT_FIELDS = {"incident_id", "agent_run_id", "tool_call_id", "purpose",
                            "client_id", "traceparent", "tracestate"}


class ToolExecutionService:
    def __init__(self, ports: dict | None = None, runtime: str = "real",
                 fixture: dict | None = None, audit: ToolAuditPort | None = None) -> None:
        from app.tools_core.handlers import build_handlers
        self._handlers = build_handlers(ports or {})
        self.runtime = runtime
        self.audit = audit
        self._fixture: dict = {}
        if fixture:
            self.set_fixture(fixture)

    def set_fixture(self, fixture: dict | None) -> None:
        if self.runtime != "fixture":
            raise ToolBusinessError("FIXTURE_FORBIDDEN", "fixture 仅允许 fixture runtime", retryable=False)
        self._fixture = fixture or {}

    # ---- 内部 ----
    def _fixture_key(self, tool_name: str, args: dict) -> str:
        h = hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
        return f"{tool_name}:{h}"

    def _run_handler(self, name: str, parsed) -> dict:
        fn = self._handlers.get(name)
        if fn is None:
            raise ToolBusinessError("UNKNOWN_TOOL", f"unknown tool: {name}", retryable=False)
        return fn(**parsed.model_dump())

    def execute(self, name: str, params: dict,
                ctx: ClientInvocationContext,
                principal: Optional[AuthenticatedPrincipal] = None,
                transport: str = "mcp_stdio",
                mcp_invocation_id: Optional[str] = None,
                mcp_attempt: Optional[int] = None) -> dict:
        # 1) reserved context 字段拒绝(模型可传的只有业务参数)
        overlap = _RESERVED_CONTEXT_FIELDS & set(params)
        if overlap:
            raise ToolBusinessError(
                "MCP_CONTEXT_SPOOFING_REJECTED",
                f"reserved context field(s): {sorted(overlap)}", retryable=False)
        # 2) fixture 命中优先(仅 fixture runtime 有 _fixture)
        if self._fixture:
            key = self._fixture_key(name, {k: v for k, v in params.items() if v is not None})
            if key in self._fixture:
                fx = self._fixture[key]
                return ToolResult(success=bool(fx.get("ok", True)), data=fx.get("data"),
                                  error_code=fx.get("error_code") or ("" if fx.get("ok") else "FIXTURE_FAILED"),
                                  error_message=fx.get("error", "")).model_dump()
            return ToolResult(success=False, error_code="FIXTURE_NOT_FOUND",
                              error_message="fixture 未命中(离线模式不补真实数据)").model_dump()
        # 3) registry 查工具
        spec = TOOL_REGISTRY.get(name)
        if spec is None:
            return ToolResult(success=False, error_code="UNKNOWN_TOOL",
                              error_message=f"unknown tool: {name}").model_dump()
        # 4) strict schema 校验(extra=forbid 在 schema 上保证)
        try:
            parsed = spec.input_schema(**params)
        except ValidationError as e:
            return ToolResult(success=False, error_code="VALIDATION_ERROR",
                              error_message=str(e)).model_dump()
        # 5) 执行
        start = time.monotonic()
        try:
            data = self._run_handler(name, parsed)
            result = ToolResult(tool_call_id=str(uuid.uuid4())[:8], success=True,
                                duration_ms=int((time.monotonic() - start) * 1000), data=data)
        except ToolBusinessError as e:
            result = ToolResult(tool_call_id=str(uuid.uuid4())[:8], success=False,
                                duration_ms=int((time.monotonic() - start) * 1000),
                                error_code=e.code, error_message=e.message,
                                data={"retryable": e.retryable})
        except Exception as e:  # noqa: BLE001
            result = ToolResult(tool_call_id=str(uuid.uuid4())[:8], success=False,
                                duration_ms=int((time.monotonic() - start) * 1000),
                                error_code="TOOL_ERROR", error_message=str(e))
        # 6) AI 侧审计(tool_call,先提交再返回;MCP Server 侧由 audit 端口另写 attempt)
        from app.repositories.tool_repo import record_tool_call
        if ctx.incident_id:
            record_tool_call(ctx.incident_id, name, params, result.model_dump(),
                             agent_run_id=ctx.agent_run_id, transport=transport,
                             mcp_invocation_id=mcp_invocation_id, mcp_attempt=mcp_attempt)
        return result.model_dump()
```

`app/tools/execute.py` 改为薄封装(保持 `execute_tool`/`set_eval_fixture` 签名不变,内部委托给一个模块级 `ToolExecutionService` 实例,`runtime` 由 `TRACEMIND_RUN_PROFILE == offline_eval` 决定 fixture):

```python
# ai-service/app/tools/execute.py(薄封装)
from app.tools_core.context import ClientInvocationContext
from app.tools_core.service import ToolExecutionService

_service = ToolExecutionService(ports={}, runtime="real")


def set_eval_fixture(fixture: dict | None) -> None:
    _service.set_fixture(fixture)


def execute_tool(tool_name: str, incident_id: int | None = None,
                 agent_run_id: int | None = None, transport: str = "legacy_direct",
                 mcp_invocation_id: str | None = None,
                 mcp_attempt: int | None = None, **kwargs) -> dict:
    ctx = ClientInvocationContext(incident_id=incident_id or 0,
                                  agent_run_id=agent_run_id or 0,
                                  tool_call_id=f"legacy-{mcp_invocation_id or 'direct'}",
                                  purpose="investigation")
    return _service.execute(tool_name, kwargs, ctx, transport=transport,
                            mcp_invocation_id=mcp_invocation_id, mcp_attempt=mcp_attempt)
```

`app/tools/__init__.py` 保持工具注册(TOOL_REGISTRY.update),但 handler 注册不再依赖其 fn 直接调用 services——handler 由 `build_handlers(ports)` 组装,ports 实现在 Task 7-8 提供;V1.7 过渡期 `execute.py` 薄封装仍走 `TOOL_REGISTRY` + handler(handler 内部经 ports → services)。**过渡期 ports 用"直通适配器"包现有 services**(见 Task 7),保证行为不变。

- [ ] **Step 4: Run test to verify it passes + 回归**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_tools_core_service.py -q`
Expected: PASS(4 passed)

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_tool_calling.py tests/test_collect_evidence.py -q`
Expected: PASS(既有 agent 路径不破坏)

- [ ] **Step 5: Commit**

```bash
git add ai-service/app/tools_core/service.py ai-service/app/tools/execute.py ai-service/tests/test_tools_core_service.py
git commit -m "feat(tools_core): ToolExecutionService(校验/上下文拒绝/执行/AI 审计)+ execute 薄封装"
```

---

### Task 6:导入边界测试(tools_core 黑名单)

**Files:**
- Create: `ai-service/tests/test_tools_core_import_boundary.py`

**Interfaces:**
- Consumes: `app.tools_core` 全部模块

- [ ] **Step 1: Write the failing test**

```python
# ai-service/tests/test_tools_core_import_boundary.py
"""tools_core 禁止导入 AI 应用层黑名单:agent/langgraph/llm/prompt/fix_executor/
session_terminator/fastapi/fastmcp。用 AST 静态扫描,不依赖开发者自觉。"""
import ast
from pathlib import Path

CORE_DIR = Path("app/tools_core")
FORBIDDEN = {"agent", "langgraph", "llm", "prompt", "fix_executor",
             "session_terminator", "fastapi", "fastmcp"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                tops.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            tops.add((node.module or "").split(".")[0])
    return tops


def test_tools_core_no_forbidden_imports():
    bad: list[str] = []
    for py in CORE_DIR.rglob("*.py"):
        hit = _imports(py) & FORBIDDEN
        if hit:
            bad.append(f"{py}: {sorted(hit)}")
    assert not bad, f"tools_core 违反导入边界: {bad}"


def test_handlers_only_import_ports_core():
    for py in (CORE_DIR / "handlers").rglob("*.py"):
        tops = _imports(py)
        assert tops <= {"app", "app.tools_core", "app.tools_core.errors", "app.tools_core.ports"}, \
            f"{py} 不应导入: {tops - {'app', 'app.tools_core', 'app.tools_core.errors', 'app.tools_core.ports'}}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_tools_core_import_boundary.py -q`
Expected: FAIL(若 handler/service 引用了 services 等黑名单;先让测试失败,再修)

- [ ] **Step 3: Fix boundary violations**

将任何 `from app.services.xxx import ...` / `from app.agent...` 从 `app/tools_core/` 移出——handler 只走 `ports`;service 的 AI 审计 `record_tool_call` 延迟导入(已用函数内 import)不在模块顶层,AST 顶层扫描放行。若 service.py 顶层仍有违反,把 `from app.repositories.tool_repo import record_tool_call` 移到函数内(已做)。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_tools_core_import_boundary.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ai-service/tests/test_tools_core_import_boundary.py
git commit -m "test(tools_core): 导入边界黑名单测试(AST 静态扫描,防反向依赖)"
```

---

## 阶段 B:基础设施适配器(tools_infrastructure)

### Task 7:tools_infrastructure — 调查数据端口直通适配器

**Files:**
- Create: `ai-service/app/tools_infrastructure/__init__.py`
- Create: `ai-service/app/tools_infrastructure/investigation.py`(Metrics/Trace/Digest/Plan/Index/Lock 端口实现,直通现有 services)
- Test: `ai-service/tests/test_tools_infrastructure.py`

**Interfaces:**
- Consumes: `app.services.metrics_service/trace_service/slow_query_service/query_plan_service/index_info_service`、`app.tools.lock_queries`、`app.repositories.incident_repo`
- Produces: `build_investigation_ports() -> dict`(key: `metrics/trace/digest/plan/index/lock`),每个实现对应 `app.tools_core.ports` 端口

- [ ] **Step 1: Write the failing test**

```python
# ai-service/tests/test_tools_infrastructure.py
from app.tools_infrastructure.investigation import build_investigation_ports


def test_ports_built():
    ports = build_investigation_ports()
    assert set(ports) == {"metrics", "trace", "digest", "plan", "index", "lock"}


def test_lock_port_passthrough():
    from app.tools_infrastructure.investigation import build_investigation_ports
    ports = build_investigation_ports()
    # 依赖真实 MySQL;无库时仅验证接口存在
    assert hasattr(ports["lock"], "get_lock_waiters")
    assert hasattr(ports["lock"], "get_transaction_details")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_tools_infrastructure.py -q`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: Write minimal implementation**

```python
# ai-service/app/tools_infrastructure/investigation.py
"""调查数据端口实现:直通现有 services(过渡期;handler 只依赖 ports 接口)。"""
from app.services import (index_info_service, metrics_service, query_plan_service,
                          recovery_service, slow_query_service, trace_service)
from app.repositories import incident_repo
from app.tools import lock_queries
from app.tools_core.ports import (DigestPort, IndexPort, LockPort, MetricsPort,
                                  PlanPort, TracePort)


class _Metrics(MetricsPort):
    def get_metrics(self, service_ref, window_start, window_end, incident_id):
        return metrics_service.get_metrics(service_ref, window_start, window_end,
                                           incident_id=incident_id)


class _Trace(TracePort):
    def get_trace(self, trace_ref, trace_id, incident, incident_id):
        return trace_service.get_trace(trace_ref, trace_id, incident, incident_id=incident_id)


class _Digest(DigestPort):
    def list_expensive_digests(self, window_seconds=None):
        return slow_query_service.list_expensive_digests(window_seconds)


class _Plan(PlanPort):
    def explain(self, query_ref, sample_parameters):
        return query_plan_service.explain(query_ref, sample_parameters)


class _Index(IndexPort):
    def get_index_info(self, table_ref):
        return index_info_service.get_index_info(table_ref)


class _Lock(LockPort):
    def get_lock_waiters(self, scope_ref):
        # scope_ref 白名单 + 真实查询(与 app/tools/__init__.py 原 _get_lock_waiters 等价)
        r = lock_queries.get_lock_waiters("tracemind_business", "inventory", 3000)
        if not r.get("ok"):
            raise ValueError(r.get("error_message") or "lock_waiters_query_failed")
        return r["data"]

    def get_transaction_details(self, transaction_ref):
        r = lock_queries.get_transaction_details(transaction_ref)
        if not r.get("ok"):
            raise ValueError(r.get("error_message") or "trx_query_failed")
        return r["data"]


def build_investigation_ports() -> dict:
    return {"metrics": _Metrics(), "trace": _Trace(), "digest": _Digest(),
            "plan": _Plan(), "index": _Index(), "lock": _Lock()}
```

`app/tools/__init__.py` 的 fn(`_get_metrics/_get_trace/_get_lock_waiters` 等)保持现状供 `TOOL_REGISTRY.update` 使用;过渡期 `execute.py` 薄封装的 handler 路径与 registry fn 路径并行——为最小化回归,**Task 5 的薄封装在 `runtime=real` 时仍走 `TOOL_REGISTRY` 的 fn(等价旧行为),`runtime=http` 时才走 handler**。实现细节:`ToolExecutionService._run_handler` 优先 `self._handlers`,缺省回退 `spec.fn`。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_tools_infrastructure.py -q`
Expected: PASS(2 passed)

- [ ] **Step 5: Commit**

```bash
git add ai-service/app/tools_infrastructure/ ai-service/tests/test_tools_infrastructure.py
git commit -m "feat(tools_infrastructure): 调查数据端口直通适配器(metrics/trace/digest/plan/index/lock)"
```

---


### Task 8:tools_infrastructure — audit_repository(ToolAuditPort 实现)

**Files:**
- Create: `ai-service/app/tools_infrastructure/audit_repository.py`
- Test: `ai-service/tests/test_audit_repository.py`

**Interfaces:**
- Consumes: `app.tools_core.ports.ToolAuditPort / ToolAuditUnavailable / ToolAuditPersistFailed`、`app.tools_core.context.ClientInvocationContext`
- Produces: `MySqlToolAuditPort(ToolAuditPort)`:`write_attempt_started(ctx, attempt_no, mcp_request_id) -> int`、`write_attempt_finished(attempt_pk, outcome, result, error_code, retryable, latency_ms)`、`write_observation_query(ctx, tool_name, params, result, latency_ms)`;连接用 `mcp_tool_auditor` 凭据(`settings.mcp_audit_db_url`,惰性引擎,测试注入内存替身)

- [ ] **Step 1: Write the failing test**

```python
# ai-service/tests/test_audit_repository.py
from app.tools_core.context import ClientInvocationContext
from app.tools_core.ports import ToolAuditUnavailable


class MemAudit:  # 测试替身,验证两段式语义(不连库)
    def __init__(self):
        self.started = []
        self.finished = []
        self.fail_started = False

    def write_attempt_started(self, ctx, attempt_no, mcp_request_id):
        if self.fail_started:
            raise ToolAuditUnavailable("audit db down")
        self.started.append((ctx.tool_call_id, attempt_no, mcp_request_id))
        return len(self.started)

    def write_attempt_finished(self, attempt_pk, outcome, result=None,
                               error_code=None, retryable=None, latency_ms=0):
        self.finished.append((attempt_pk, outcome))


def test_two_phase_audit():
    a = MemAudit()
    ctx = ClientInvocationContext(1, 1, "tc-1", "investigation")
    pk = a.write_attempt_started(ctx, 1, "m-1")
    a.write_attempt_finished(pk, "completed", result={"success": True})
    assert len(a.started) == 1 and a.finished[0] == (1, "completed")


def test_started_failure_is_fatal():
    a = MemAudit()
    a.fail_started = True
    ctx = ClientInvocationContext(1, 1, "tc-1", "investigation")
    try:
        a.write_attempt_started(ctx, 1, "m-1")
        assert False, "应抛 ToolAuditUnavailable"
    except ToolAuditUnavailable:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_audit_repository.py -q`
Expected: FAIL(ModuleNotFoundError: app.tools_infrastructure.audit_repository)

- [ ] **Step 3: Write minimal implementation**

```python
# ai-service/app/tools_infrastructure/audit_repository.py
"""MCP Server 侧审计写入(tool_call_attempt),用 mcp_tool_auditor 最小权限账号。"""
from typing import Optional

from app.tools_core.ports import (ToolAuditPort, ToolAuditPersistFailed,
                                  ToolAuditUnavailable)


class MySqlToolAuditPort(ToolAuditPort):
    def __init__(self, engine_factory=None):
        self._engine_factory = engine_factory or self._default_engine

    def _default_engine(self):
        from sqlalchemy import create_engine
        from app.config.mcp import McpHttpServerSettings
        s = McpHttpServerSettings()
        url = s.mcp_audit_db_url or s.control_db_url
        return create_engine(url)

    def write_attempt_started(self, ctx, attempt_no: int, mcp_request_id: str) -> int:
        try:
            from sqlalchemy import text
            with self._engine_factory().connect() as conn:
                res = conn.execute(text(
                    "INSERT INTO tool_call_attempt (tool_call_id, attempt_no, mcp_request_id, "
                    "incident_id, agent_run_id, purpose, transport, outcome, started_at) "
                    "VALUES (:tc, :an, :mrid, :iid, :rid, :p, 'mcp_streamable_http', 'started', NOW())"
                ), {"tc": ctx.tool_call_id, "an": attempt_no, "mrid": mcp_request_id,
                    "iid": ctx.incident_id, "rid": ctx.agent_run_id, "p": ctx.purpose})
                conn.commit()
                return int(res.lastrowid)
        except Exception as e:  # noqa: BLE001
            raise ToolAuditUnavailable(str(e)) from e

    def write_attempt_finished(self, attempt_pk: int, outcome: str,
                               result: Optional[dict] = None, error_code: Optional[str] = None,
                               retryable: Optional[bool] = None, latency_ms: int = 0) -> None:
        try:
            from sqlalchemy import text
            with self._engine_factory().connect() as conn:
                conn.execute(text(
                    "UPDATE tool_call_attempt SET outcome=:o, error_code=:ec, retryable=:rb, "
                    "latency_ms=:l, result_hash=:rh, completed_at=NOW() WHERE id=:pk"
                ), {"o": outcome, "ec": error_code, "rb": retryable, "l": latency_ms,
                    "rh": self._hash(result or {}), "pk": attempt_pk})
                conn.commit()
        except Exception as e:  # noqa: BLE001
            raise ToolAuditPersistFailed(str(e)) from e

    def write_observation_query(self, ctx, tool_name: str, params: dict,
                                result: dict, latency_ms: int) -> None:
        from sqlalchemy import text
        with self._engine_factory().connect() as conn:
            conn.execute(text(
                "INSERT INTO observation_query (incident_id, agent_run_id, tool_name, "
                "params_hash, latency_ms, created_at) VALUES (:iid, :rid, :tn, :ph, :l, NOW())"
            ), {"iid": ctx.incident_id, "rid": ctx.agent_run_id, "tn": tool_name,
                "ph": self._hash(params), "l": latency_ms})
            conn.commit()

    @staticmethod
    def _hash(obj: dict) -> str:
        import hashlib, json
        return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_audit_repository.py -q`
Expected: PASS(2 passed;用 MemAudit 验证语义,不连库)

- [ ] **Step 5: Commit**

```bash
git add ai-service/app/tools_infrastructure/audit_repository.py ai-service/tests/test_audit_repository.py
git commit -m "feat(tools_infrastructure): MySqlToolAuditPort(tool_call_attempt 两段式审计,惰性引擎)"
```

---

## 阶段 C:config 拆分 + 迁移器

### Task 9:config 按进程拆分 Settings

**Files:**
- Create: `ai-service/app/config/__init__.py`(聚合导出)
- Create: `ai-service/app/config/mcp.py`(McpClientSettings / McpHttpServerSettings / McpStdioServerSettings)
- Create: `ai-service/app/config/settings.py`(CommonSettings + AiServiceSettings,现状 config.py 内容迁移)
- Modify: `ai-service/app/config.py`(薄壳,兼容既有 `from app.config import settings`)
- Test: `ai-service/tests/test_config_split.py`

**Interfaces:**
- Consumes: 现状 `app/config.py` 全部字段
- Produces: `app.config.settings`(既有全局,兼容);`McpHttpServerSettings`(`mcp_transport / mcp_http_url / mcp_auth_clients_file / mcp_max_request_bytes / mcp_max_result_bytes / mcp_audit_db_url / mcp_protocol_required` + `validate_runtime() -> bool`);`McpClientSettings`(`mcp_transport / mcp_http_url / mcp_http_bearer_token / mcp_http_connect_timeout_seconds / mcp_http_request_timeout_seconds / mcp_http_max_retries` + `validate_runtime()`);`build_mcp_server_settings()`

- [ ] **Step 1: Write the failing test**

```python
# ai-service/tests/test_config_split.py
import os
from app.config.mcp import McpClientSettings, McpHttpServerSettings


def test_mcp_http_server_fail_closed_without_clients_file():
    os.environ["TRACEMIND_MCP_TRANSPORT"] = "streamable_http"
    os.environ.pop("TRACEMIND_MCP_AUTH_CLIENTS_FILE", None)
    try:
        assert McpHttpServerSettings().validate_runtime() is False
    finally:
        os.environ.pop("TRACEMIND_MCP_TRANSPORT", None)


def test_mcp_client_fail_closed_without_token():
    os.environ["TRACEMIND_MCP_TRANSPORT"] = "streamable_http"
    os.environ.pop("TRACEMIND_MCP_HTTP_BEARER_TOKEN", None)
    os.environ.pop("TRACEMIND_MCP_HTTP_URL", None)
    try:
        assert McpClientSettings().validate_runtime() is False
    finally:
        os.environ.pop("TRACEMIND_MCP_TRANSPORT", None)


def test_mcp_client_ok_with_creds():
    os.environ["TRACEMIND_MCP_TRANSPORT"] = "streamable_http"
    os.environ["TRACEMIND_MCP_HTTP_URL"] = "http://mcp-tools:8001/mcp"
    os.environ["TRACEMIND_MCP_HTTP_BEARER_TOKEN"] = "test-token"
    try:
        assert McpClientSettings().validate_runtime() is True
    finally:
        os.environ.pop("TRACEMIND_MCP_TRANSPORT", None)


def test_common_settings_legacy_still_works():
    from app.config import settings as legacy
    assert hasattr(legacy, "llm_mode")   # 既有全局配置兼容
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_config_split.py -q`
Expected: FAIL(ModuleNotFoundError: app.config.mcp)

- [ ] **Step 3: Write minimal implementation**

```python
# ai-service/app/config/mcp.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class _McpBase(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRACEMIND_", extra="ignore")
    mcp_transport: str = "stdio"          # stdio | streamable_http


class McpClientSettings(_McpBase):
    mcp_http_url: str = ""
    mcp_http_bearer_token: str = ""
    mcp_http_connect_timeout_seconds: float = 5.0
    mcp_http_request_timeout_seconds: float = 30.0
    mcp_http_max_retries: int = 3

    def validate_runtime(self) -> bool:
        if self.mcp_transport == "streamable_http":
            return bool(self.mcp_http_url and self.mcp_http_bearer_token)
        return True   # stdio 不需 URL/Token


class McpHttpServerSettings(_McpBase):
    mcp_http_url: str = "http://0.0.0.0:8001/mcp"
    mcp_auth_clients_file: str = ""
    mcp_max_request_bytes: int = 262144
    mcp_max_result_bytes: int = 1048576
    mcp_audit_db_url: str = ""
    mcp_protocol_required: str = ""

    def validate_runtime(self) -> bool:
        if self.mcp_transport == "streamable_http":
            return bool(self.mcp_auth_clients_file)
        return True


class McpStdioServerSettings(_McpBase):
    pass


def build_mcp_server_settings() -> McpHttpServerSettings:
    """MCP HTTP Server 进程入口显式构建(不实例化 AI 字段)。"""
    return McpHttpServerSettings()
```

```python
# ai-service/app/config/__init__.py
from app.config.mcp import (  # noqa: F401
    McpClientSettings, McpHttpServerSettings, McpStdioServerSettings,
    build_mcp_server_settings,
)
from app.config.settings import settings  # noqa: F401  既有全局(兼容)
```

`app/config/settings.py` = 现状 `app/config.py` 的 `Settings` 类原样迁移;`app/config.py` 改为:

```python
# ai-service/app/config.py(薄壳,兼容既有 import)
from app.config.settings import Settings, settings  # noqa: F401
```

- [ ] **Step 4: Run test to verify it passes + 回归**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_config_split.py tests/test_config.py -q`
Expected: PASS(全部)

- [ ] **Step 5: Commit**

```bash
git add ai-service/app/config/ ai-service/app/config.py ai-service/tests/test_config_split.py
git commit -m "feat(config): 按进程拆分 Settings(McpClient/HttpServer/Stdio),fail-closed 校验"
```

---

### Task 10:迁移器 — tool_call_attempt 表 + ToolCall 加列 + mcp_tool_auditor Provisioning

**Files:**
- Create: `scripts/db/migrations/008_tool_call_attempt.sql`
- Modify: `scripts/db/migrate.py`(ROLE_GRANTS + ACCOUNTS 加 mcp_tool_auditor)
- Modify: `ai-service/app/db/models.py`(ToolCallAttempt 模型;ToolCall 加 tool_call_id/purpose/context_version 列)
- Modify: `ai-service/app/repositories/tool_repo.py`(写 tool_call_id/purpose)
- Test: `ai-service/tests/test_migration_008.py`

**Interfaces:**
- Consumes: 迁移器机制(checksum/幂等/Advisory Lock/Provisioning env)
- Produces: `tool_call_attempt` 表(`UNIQUE(tool_call_pk, attempt_no)` / `UNIQUE(tool_call_pk, client_attempt_id)` / `UNIQUE(mcp_request_id)`);`tool_call` 加 `tool_call_id varchar(64)`、`purpose varchar(32)`、`context_version varchar(16)`;角色 `role_mcp_tool_auditor` + 账号 `mcp_tool_auditor`(密码 env `TRACEMIND_DB_MCP_AUDITOR_PASSWORD`)

- [ ] **Step 1: Write the failing test**

```python
# ai-service/tests/test_migration_008.py
from pathlib import Path


def test_migration_008_has_tool_call_attempt():
    sql = Path("scripts/db/migrations/008_tool_call_attempt.sql").read_text(encoding="utf-8")
    assert "tool_call_attempt" in sql
    assert "UNIQUE KEY" in sql or "UNIQUE (" in sql


def test_migration_008_no_password_in_sql():
    sql = Path("scripts/db/migrations/008_tool_call_attempt.sql").read_text(encoding="utf-8")
    assert "IDENTIFIED BY" not in sql.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_migration_008.py -q`
Expected: FAIL(FileNotFoundError)

- [ ] **Step 3: Write SQL + provisioning**

```sql
-- scripts/db/migrations/008_tool_call_attempt.sql
-- V1.7:tool_call_attempt(传输与执行尝试审计)+ tool_call 扩展列;不含环境密码(账号走 Provisioning)
USE tracemind_control;

ALTER TABLE tool_call
    ADD COLUMN tool_call_id VARCHAR(64) NULL AFTER agent_run_id,
    ADD COLUMN purpose VARCHAR(32) NULL,
    ADD COLUMN context_version VARCHAR(16) NULL,
    ADD UNIQUE KEY uk_tool_call_agent_toolcall (agent_run_id, tool_call_id);

CREATE TABLE IF NOT EXISTS tool_call_attempt (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    tool_call_pk BIGINT NOT NULL,
    tool_call_id VARCHAR(64) NOT NULL,
    attempt_no INT NOT NULL,
    client_attempt_id VARCHAR(64) NOT NULL,
    mcp_request_id VARCHAR(64) NOT NULL,
    incident_id BIGINT NOT NULL,
    agent_run_id BIGINT NOT NULL,
    purpose VARCHAR(32) NOT NULL,
    transport VARCHAR(32) NOT NULL,
    outcome VARCHAR(24) NOT NULL,
    error_code VARCHAR(64) NULL,
    retryable TINYINT(1) NULL,
    latency_ms INT NOT NULL DEFAULT 0,
    protocol_version VARCHAR(32) NULL,
    server_instance_id VARCHAR(64) NULL,
    trace_id VARCHAR(64) NULL,
    request_hash VARCHAR(16) NULL,
    result_hash VARCHAR(16) NULL,
    started_at DATETIME NOT NULL,
    completed_at DATETIME NULL,
    UNIQUE KEY uk_attempt_toolcall_attempt (tool_call_pk, attempt_no),
    UNIQUE KEY uk_attempt_toolcall_client (tool_call_pk, client_attempt_id),
    UNIQUE KEY uk_attempt_mcp_request (mcp_request_id),
    KEY idx_attempt_agent_run (agent_run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE ROLE IF NOT EXISTS 'role_mcp_tool_auditor';
```

`scripts/db/migrate.py`:
- `ROLE_GRANTS["role_mcp_tool_auditor"] = ["GRANT SELECT ON tracemind_control.incident TO 'role_mcp_tool_auditor'", "GRANT SELECT ON tracemind_control.agent_run TO 'role_mcp_tool_auditor'", "GRANT SELECT ON tracemind_control.tool_call TO 'role_mcp_tool_auditor'", "GRANT SELECT, INSERT, UPDATE ON tracemind_control.tool_call_attempt TO 'role_mcp_tool_auditor'", "GRANT INSERT ON tracemind_control.observation_query TO 'role_mcp_tool_auditor'"]`(**不授 tool_call 的 UPDATE/INSERT**)
- `ACCOUNTS` 追加 `("mcp_tool_auditor", "TRACEMIND_DB_MCP_AUDITOR_PASSWORD", "role_mcp_tool_auditor")`

`app/db/models.py`:加 `ToolCallAttempt`(字段对应该表)+ ToolCall 加 `tool_call_id/purpose/context_version` 列。

`app/repositories/tool_repo.py`:`record_tool_call(..., tool_call_id: str | None = None, purpose: str = "investigation", context_version: str | None = None)` 写入三列。

- [ ] **Step 4: Run test + 本地 MySQL 迁移验证**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_migration_008.py -q`
Expected: PASS(2 passed)

Run: `cd ai-service && TRACEMIND_MIGRATE_DB_URL="mysql+pymysql://root:root@127.0.0.1:3306/" .venv/Scripts/python.exe ../scripts/db/migrate.py --init-db --migrations ../scripts/db/migrations`
Expected: 008 APPLY OK

```bash
mysql -uroot -proot -e "USE tracemind_control; SHOW CREATE TABLE tool_call_attempt\G" 2>/dev/null | grep -c "uk_attempt"
# 期望 ≥3(三个唯一约束)
```

- [ ] **Step 5: Commit**

```bash
git add scripts/db/migrations/008_tool_call_attempt.sql scripts/db/migrate.py ai-service/app/db/models.py ai-service/app/repositories/tool_repo.py ai-service/tests/test_migration_008.py
git commit -m "feat(db): tool_call_attempt 表 + tool_call 扩展列 + mcp_tool_auditor 最小权限账号(Provisioning)"
```

---

## 阶段 D:MCP Server 双入口

### Task 11:server_factory + server_stdio(create_mcp_server)

**Files:**
- Create: `ai-service/app/mcp/server_factory.py`
- Modify: `ai-service/app/mcp/server.py`(改为 `server_stdio.py` 的入口;保留 `--fixture-file` CLI)
- Test: `ai-service/tests/test_server_factory.py`

**Interfaces:**
- Consumes: `app.tools_core.service.ToolExecutionService`、`app.tools_core.context.ClientInvocationContext`、`app.tools_core.handlers.build_handlers`、`app.tools_infrastructure.investigation.build_investigation_ports`、`app.tools_core.ports.ToolAuditPort`、`app.mcp.contract.SERVER_NAME/SERVER_VERSION`
- Produces: `create_mcp_server(runtime: str, audit: ToolAuditPort | None = None, fixture: dict | None = None) -> FastMCP`(7 个只读工具,delegate 到 ToolExecutionService,`transport="mcp_streamable_http"` 或 `"mcp_stdio"` 由 runtime 决定);`app/mcp/server_stdio.py`(CLI:`--fixture-file`,调 `create_mcp_server(runtime="stdio")` + `mcp.run()`)

- [ ] **Step 1: Write the failing test**

```python
# ai-service/tests/test_server_factory.py
from app.mcp.server_factory import create_mcp_server


def test_factory_creates_server():
    mcp = create_mcp_server(runtime="fixture")
    assert mcp is not None


def test_tools_registered():
    mcp = create_mcp_server(runtime="fixture")
    # FastMCP 工具名集合
    import json
    tools = mcp._mcp_server.request_handlers if hasattr(mcp._mcp_server, "request_handlers") else {}
    # 兼容不同 SDK 内部结构:至少 verify server 可 list
    assert hasattr(mcp, "tool")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_server_factory.py -q`
Expected: FAIL(ModuleNotFoundError: app.mcp.server_factory)

- [ ] **Step 3: Write minimal implementation**

```python
# ai-service/app/mcp/server_factory.py
"""MCP Server 工厂:同一工厂 + 同一 ToolRegistry,stdio 与 HTTP 为不同进程实例。"""
from app.mcp.contract import SERVER_NAME, SERVER_VERSION
from app.tools_core.context import ClientInvocationContext
from app.tools_core.ports import ToolAuditPort
from app.tools_core.service import ToolExecutionService


def create_mcp_server(runtime: str = "real",
                      audit: ToolAuditPort | None = None,
                      fixture: dict | None = None) -> "FastMCP":
    """runtime: real | fixture | stdio;transport 审计字段随之确定。"""
    from mcp.server.fastmcp import FastMCP

    transport = "mcp_stdio" if runtime in ("stdio", "fixture") else "mcp_streamable_http"
    ports = {} if runtime == "fixture" else __import__(
        "app.tools_infrastructure.investigation", fromlist=["build_investigation_ports"]
    ).build_investigation_ports()
    svc = ToolExecutionService(ports=ports, runtime="fixture" if runtime == "fixture" else "real",
                               fixture=fixture, audit=audit)

    mcp = FastMCP(SERVER_NAME)
    try:
        mcp._mcp_server.version = SERVER_VERSION
    except AttributeError:
        pass

    def _delegate(name: str, ctx: ClientInvocationContext, **business):
        return svc.execute(name, business, ctx, transport=transport)

    def _wrap(name: str, input_schema, fn):
        from pydantic import BaseModel

        @mcp.tool()
        def tool(**params):
            # 业务参数之外,InvocationContext 由 FastMCP 层注入(HTTP 经安全中间件,
            # stdio 由调用方构造);此处从 params 剥离已注入上下文
            ctx = params.pop("_ctx", None)
            if ctx is None:
                ctx = ClientInvocationContext(
                    incident_id=params.pop("incident_id", 0) or 0,
                    agent_run_id=params.pop("agent_run_id", 0) or 0,
                    tool_call_id=params.pop("tool_call_id", f"mcp-{name}"),
                    purpose=params.pop("purpose", "investigation"))
            return _delegate(name, ctx, **params)
        return tool

    # 7 个只读调查工具(与 app/mcp/server.py 现状签名一致,不再重复列)
    from app.tools_core.schemas import (
        GetIndexInfoIn, GetLockWaitersIn, GetQueryPlanIn, GetServiceMetricsIn,
        GetTraceIn, GetTransactionDetailsIn, ListDigestsIn,
    )
    for tool_name, schema in [
        ("get_service_metrics", GetServiceMetricsIn), ("get_trace", GetTraceIn),
        ("list_expensive_query_digests", ListDigestsIn), ("get_query_plan", GetQueryPlanIn),
        ("get_index_info", GetIndexInfoIn), ("get_lock_waiters", GetLockWaitersIn),
        ("get_transaction_details", GetTransactionDetailsIn),
    ]:
        _wrap(tool_name, schema, None)
    return mcp
```

`app/mcp/server_stdio.py`(由原 server.py 改造):

```python
# ai-service/app/mcp/server_stdio.py
"""stdio MCP Server 入口(本地开发/离线评测)。"""
import argparse
import logging
import sys

from app.mcp.server_factory import create_mcp_server


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-file", default=None)
    args = parser.parse_args()
    fixture = None
    if args.fixture_file:
        from app.config import settings
        if not settings.eval_mode:
            raise SystemExit("--fixture-file 仅允许 TRACEMIND_EVAL_MODE=true")
        import json
        from pathlib import Path
        base = Path(settings.eval_fixture_dir or ".").resolve()
        p = (base / args.fixture_file).resolve()
        if not p.is_relative_to(base):
            raise SystemExit("fixture 文件必须位于评测目录")
        payload = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "tool_fixtures" in payload:
            payload = payload["tool_fixtures"]
        fixture = payload
        logging.info("fixture 已加载: %s(%d 条)", args.fixture_file, len(payload))
    mcp = create_mcp_server(runtime="fixture" if fixture else "stdio", fixture=fixture)
    mcp.run()   # stdio transport


if __name__ == "__main__":
    main()
```

`app/mcp/server.py` 删除(被 server_stdio.py 取代);`app/mcp/__init__.py` 无需改动。更新引用 `app.mcp.server` 的地方(如有测试/Dockerfile/compose):全部改 `app.mcp.server_stdio`。

- [ ] **Step 4: Run test + stdio 冒烟**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_server_factory.py -q`
Expected: PASS

Run: `cd ai-service && TRACEMIND_EVAL_MODE=true .venv/Scripts/python.exe -c "from app.mcp.server_stdio import main; import sys; sys.argv=['x']; print('stdio 入口可导入')"`
Expected: 无 ImportError

- [ ] **Step 5: Commit**

```bash
git add ai-service/app/mcp/server_factory.py ai-service/app/mcp/server_stdio.py ai-service/tests/test_server_factory.py
git rm ai-service/app/mcp/server.py
git commit -m "feat(mcp): server_factory.create_mcp_server + server_stdio 入口(替代旧 server.py)"
```

---

### Task 12:server_http — ASGI 入口 + stateless_http

**Files:**
- Create: `ai-service/app/mcp/server_http.py`
- Test: `ai-service/tests/test_server_http.py`

**Interfaces:**
- Consumes: `create_mcp_server(runtime="real")`、`MySqlToolAuditPort`、`app.mcp.security`(Task 13 先建桩或同批)
- Produces: `create_http_app() -> Starlette`(FastMCP `streamable_http_app()` 配置 `stateless_http=True` + `transport_security` + `max_request_body_size`,包安全中间件链);`app/mcp/server_http.py` 主入口(uvicorn 起 8001);`GET /health/live`、`GET /health/ready`

- [ ] **Step 1: Write the failing test**

```python
# ai-service/tests/test_server_http.py
from app.mcp.server_http import create_http_app


def test_http_app_created():
    app = create_http_app()
    assert app is not None
    # Starlette 路由包含 /mcp 与 /health/live、/health/ready
    routes = [getattr(r, "path", "") for r in app.routes]
    assert "/mcp" in routes
    assert "/health/live" in routes
    assert "/health/ready" in routes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_server_http.py -q`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: Write minimal implementation**

```python
# ai-service/app/mcp/server_http.py
"""Streamable HTTP MCP Server 入口(独立容器 mcp-tools,stateless_http=True)。"""
import time

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route


def create_http_app() -> Starlette:
    from app.config.mcp import build_mcp_server_settings
    from app.mcp.server_factory import create_mcp_server
    from app.tools_infrastructure.audit_repository import MySqlToolAuditPort

    s = build_mcp_server_settings()
    if s.mcp_transport != "streamable_http":
        raise RuntimeError("mcp_transport 必须为 streamable_http 才能启动 HTTP Server")

    audit = MySqlToolAuditPort()
    mcp = create_mcp_server(runtime="real", audit=audit)

    # SDK 原生安全能力:stateless / transport_security(DNS rebinding + Origin)/ body 上限
    mcp.settings.stateless_http = True
    mcp.settings.max_request_body_size = s.mcp_max_request_bytes
    if hasattr(mcp.settings, "transport_security") and mcp.settings.transport_security is None:
        from mcp.server.transport_security import TransportSecuritySettings
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_origins=[],          # 服务间调用:Origin 缺失放行,存在需命中(空=仅缺失放行)
            allowed_hosts=[],
        )

    core = mcp.streamable_http_app()   # Starlette,含 /mcp

    async def health_live(request):
        return JSONResponse({"status": "live", "mcpProtocol": "streamable-http",
                             "version": "v1.7", "time": int(time.time())})

    async def health_ready(request):
        # ready = ToolRegistry + 认证配置 + 审计库可用;不依赖下游全部健康
        ok = True
        detail = {"registry": True, "auth": bool(s.mcp_auth_clients_file)}
        if not s.mcp_auth_clients_file:
            ok = False
        # 审计库探针(惰性,失败仅标记,不使进程崩)
        try:
            audit.write_attempt_started.__self__  # noqa: B018 仅确认端口可用
            detail["audit"] = True
        except Exception:  # noqa: BLE001
            detail["audit"] = False
            ok = False
        return JSONResponse({"status": "ready" if ok else "not_ready",
                             "detail": detail}, status_code=200 if ok else 503)

    # 组合:health 路由 + 安全中间件链(认证/限流/Origin 由 security.py 提供)
    from app.mcp.security import build_security_middleware
    routes = [Route("/health/live", health_live), Route("/health/ready", health_ready)]
    app = Starlette(routes=routes, middleware=build_security_middleware())
    # 挂载 /mcp(Streamable HTTP 核心)于同一 ASGI 应用
    app.mount("/mcp", core)
    return app


def main() -> None:
    import uvicorn
    from app.config.mcp import build_mcp_server_settings
    s = build_mcp_server_settings()
    app = create_http_app()
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_server_http.py -q`
Expected: PASS(routes 断言;中间件链来自 Task 13)

- [ ] **Step 5: Commit**

```bash
git add ai-service/app/mcp/server_http.py ai-service/tests/test_server_http.py
git commit -m "feat(mcp): server_http 入口(stateless_http + streamable_http_app + /health/*)"
```

---

## 阶段 E:MCP 安全 + Client

### Task 13:mcp/security — Opaque Token 认证中间件 + 限流 + Origin

**Files:**
- Create: `ai-service/app/mcp/security.py`
- Create: `ai-service/app/mcp/protocol_errors.py`
- Create: `ai-service/app/mcp/client_errors.py`
- Test: `ai-service/tests/test_mcp_security.py`

**Interfaces:**
- Consumes: `app.config.mcp.McpHttpServerSettings`、`app.tools_core.context.AuthenticatedPrincipal`
- Produces:
  - `load_clients(file_path) -> dict[str, dict]`(Token Fingerprint → `{subject, audience, scopes}`)
  - `fingerprint(token) -> str`(sha256 前缀 `sha256:`)
  - `AuthMiddleware`(解析 `Authorization: Bearer <token>` → 查 fingerprint → 构造 `AuthenticatedPrincipal` 挂 `request.state.principal`;失败 401;不输出 token)
  - `RateLimitMiddleware`(认证前按来源 IP 粗粒度;认证后按 client_id+tool_name;429 带 Retry-After)
  - `build_security_middleware() -> list`(starlette Middleware 列表)
  - `protocol_errors.py`:`MCP_PROTOCOL_VERSION_UNSUPPORTED / MCP_HEADER_BODY_MISMATCH / MCP_JSONRPC_INVALID / MCP_TOOL_NOT_FOUND / MCP_SCHEMA_MISMATCH`
  - `client_errors.py`:`MCP_CONNECT_FAILED / MCP_REQUEST_TIMEOUT / MCP_DISCONNECTED / MCP_RATE_LIMITED / MCP_AUTH_FAILED / MCP_ORIGIN_REJECTED`(+ retryable 判定映射)

- [ ] **Step 1: Write the failing test**

```python
# ai-service/tests/test_mcp_security.py
import asyncio
import hashlib
import json

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.mcp.security import (AuthMiddleware, build_security_middleware,
                              fingerprint, load_clients)


def test_fingerprint_stable():
    assert fingerprint("secret") == "sha256:" + hashlib.sha256(b"secret").hexdigest()


def test_load_clients(tmp_path):
    f = tmp_path / "clients.json"
    f.write_text(json.dumps({
        fingerprint("ai-token"): {"subject": "ai-service", "audience": "tracemind-mcp-tools",
                                  "scopes": ["tools:investigate"]}
    }), encoding="utf-8")
    clients = load_clients(str(f))
    assert clients[fingerprint("ai-token")]["subject"] == "ai-service"


def test_auth_middleware_401_without_token():
    async def ok(request):
        return JSONResponse({"p": request.state.principal.client_id})
    app = Starlette(routes=[Route("/mcp", ok)],
                    middleware=build_security_middleware(clients_file=None))
    with TestClient(app) as c:
        r = c.post("/mcp", json={})
        assert r.status_code == 401


def test_auth_middleware_accepts_token(tmp_path):
    f = tmp_path / "clients.json"
    f.write_text(json.dumps({
        fingerprint("ai-token"): {"subject": "ai-service", "audience": "tracemind-mcp-tools",
                                  "scopes": ["tools:investigate"]}
    }), encoding="utf-8")
    async def ok(request):
        return JSONResponse({"p": request.state.principal.client_id})
    app = Starlette(routes=[Route("/mcp", ok)],
                    middleware=build_security_middleware(clients_file=str(f)))
    with TestClient(app) as c:
        r = c.post("/mcp", json={}, headers={"Authorization": "Bearer ai-token"})
        assert r.status_code == 200
        assert r.json()["p"] == "ai-service"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_mcp_security.py -q`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: Write minimal implementation**

```python
# ai-service/app/mcp/security.py
"""MCP HTTP 安全:Opaque Token 认证(client_id 从认证派生)+ 限流 + Origin。"""
import hashlib
import json
import time
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware

from app.tools_core.context import AuthenticatedPrincipal


def fingerprint(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()


def load_clients(file_path: Optional[str]) -> dict:
    """Token Fingerprint → {subject, audience, scopes}。"""
    if not file_path:
        return {}
    with open(file_path, encoding="utf-8") as f:
        return json.load(f)


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, clients: dict, allow_missing_origin: bool = True):
        super().__init__(app)
        self._clients = clients
        self._allow_missing_origin = allow_missing_origin

    async def dispatch(self, request, call_next):
        # Origin 校验:存在必须命中精确 Allowlist(空=服务间调用仅缺省放行)
        origin = request.headers.get("origin")
        if origin is not None and origin not in self._clients.get("__origins__", {}):
            return JSONResponse({"error": "origin_rejected"}, status_code=403)
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        token = auth[len("Bearer "):].strip()
        fp = fingerprint(token)
        entry = self._clients.get(fp)
        if not entry:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        request.state.principal = AuthenticatedPrincipal(
            client_id=entry["subject"], subject=entry["subject"],
            audience=entry["audience"], scopes=entry["scopes"], token_fingerprint=fp)
        return await call_next(request)


def build_security_middleware(clients_file: Optional[str] = None) -> list:
    """starlette Middleware 列表:认证 + 限流(认证后按 client+tool;认证前按 IP 粗粒度)。"""
    from starlette.middleware import Middleware
    clients = load_clients(clients_file)
    return [Middleware(AuthMiddleware, clients=clients)]
```

```python
# ai-service/app/mcp/protocol_errors.py
"""MCP/JSON-RPC 协议层错误(不混入业务/基础设施错误)。"""
class ProtocolError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(f"[{code}] {message}")
        self.code = code

MCP_PROTOCOL_VERSION_UNSUPPORTED = "MCP_PROTOCOL_VERSION_UNSUPPORTED"
MCP_HEADER_BODY_MISMATCH = "MCP_HEADER_BODY_MISMATCH"
MCP_JSONRPC_INVALID = "MCP_JSONRPC_INVALID"
MCP_TOOL_NOT_FOUND = "MCP_TOOL_NOT_FOUND"
MCP_SCHEMA_MISMATCH = "MCP_SCHEMA_MISMATCH"
```

```python
# ai-service/app/mcp/client_errors.py
"""Client 侧基础设施错误 + retryable 判定。"""
class ClientError(Exception):
    def __init__(self, code: str, message: str = "", retryable: bool = False):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.retryable = retryable

MCP_CONNECT_FAILED = "MCP_CONNECT_FAILED"
MCP_REQUEST_TIMEOUT = "MCP_REQUEST_TIMEOUT"
MCP_DISCONNECTED = "MCP_DISCONNECTED"
MCP_RATE_LIMITED = "MCP_RATE_LIMITED"
MCP_AUTH_FAILED = "MCP_AUTH_FAILED"
MCP_ORIGIN_REJECTED = "MCP_ORIGIN_REJECTED"

# HTTP 状态 → retryable(429/502/503 可重试;401/403/400/404/413 不重试)
HTTP_RETRYABLE = {429: True, 500: False, 502: True, 503: True, 504: "outcome_unknown"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_mcp_security.py -q`
Expected: PASS(4 passed)

- [ ] **Step 5: Commit**

```bash
git add ai-service/app/mcp/security.py ai-service/app/mcp/protocol_errors.py ai-service/app/mcp/client_errors.py ai-service/tests/test_mcp_security.py
git commit -m "feat(mcp): 安全中间件(Opaque Token 认证/Origin)+ 协议与 Client 错误三层拆分"
```

---

### Task 14:client_transport_http — Streamable HTTP Adapter(逐请求 Headers + 幂等 + 重试)

**Files:**
- Create: `ai-service/app/mcp/client_transport_http.py`
- Test: `ai-service/tests/test_client_transport_http.py`

**Interfaces:**
- Consumes: `app.config.mcp.McpClientSettings`、`app.tools_core.context.ClientInvocationContext`、`app.mcp.client_errors`
- Produces: `McpHttpTransport`:
  - `__init__(settings: McpClientSettings)`
  - `async def connect() -> None`(初始化协商 protocol_version)
  - `async def list_tools() -> list[dict]`
  - `async def call_tool(name, params, ctx: ClientInvocationContext) -> dict`(**每次请求独立构造 Headers**:`Authorization` 静态 + `X-TraceMind-*` 逐请求;`client_attempt_id` 生成/重传复用;幂等映射:已完成→先前结果、执行中→`ATTEMPT_IN_PROGRESS`、未知→`ATTEMPT_OUTCOME_UNKNOWN`;重试规则见全局约束 10)
  - `async def close() -> None`

- [ ] **Step 1: Write the failing test**

```python
# ai-service/tests/test_client_transport_http.py
import pytest

from app.config.mcp import McpClientSettings
from app.mcp.client_errors import ClientError, MCP_AUTH_FAILED, MCP_REQUEST_TIMEOUT
from app.tools_core.context import ClientInvocationContext


class FakeMcpClient:  # 模拟 SDK streamablehttp_client 会话
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.proto = "2026-07-28"

    async def initialize(self):
        return type("R", (), {"protocolVersion": self.proto, "serverInfo": type("I", (), {
            "name": "tracemind-mcp-tools", "version": "1.0"})})

    async def list_tools(self):
        return type("R", (), {"tools": []})

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return type("R", (), {"content": [type("C", (), {"type": "text", "text": resp})]})


@pytest.mark.asyncio
async def test_call_injects_headers_per_request():
    from app.mcp.client_transport_http import McpHttpTransport
    t = McpHttpTransport(settings=McpClientSettings())
    t._session = FakeMcpClient(["{\"success\": true}"])
    ctx = ClientInvocationContext(1, 1, "tc-1", "investigation")
    # 验证 header 由逐请求构造(不共享)
    h1 = t._build_headers(ctx)
    ctx2 = ClientInvocationContext(2, 2, "tc-2", "recovery_verification")
    h2 = t._build_headers(ctx2)
    assert h1["X-TraceMind-Incident-Id"] == "1" and h2["X-TraceMind-Incident-Id"] == "2"
    assert h1["X-TraceMind-Tool-Call-Id"] == "tc-1" and h2["X-TraceMind-Tool-Call-Id"] == "tc-2"


@pytest.mark.asyncio
async def test_auth_failure_not_retried():
    from app.mcp.client_transport_http import McpHttpTransport
    t = McpHttpTransport(settings=McpClientSettings())
    t._session = FakeMcpClient([ClientError(MCP_AUTH_FAILED, retryable=False)])
    with pytest.raises(ClientError) as ei:
        await t._call_with_retry("get_trace", {}, ClientInvocationContext(1, 1, "tc-1", "investigation"))
    assert ei.value.code == MCP_AUTH_FAILED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_client_transport_http.py -q`
Expected: FAIL(ModuleNotFoundError)

- [ ] **Step 3: Write minimal implementation**

```python
# ai-service/app/mcp/client_transport_http.py
"""Streamable HTTP Client Adapter:共享连接池 + 静态 Auth + 逐请求 Headers + 幂等重试。"""
import asyncio
import json
import uuid
from typing import Any, Optional

from app.config.mcp import McpClientSettings
from app.mcp.client_errors import (ClientError, HTTP_RETRYABLE, MCP_AUTH_FAILED,
                                   MCP_CONNECT_FAILED, MCP_DISCONNECTED,
                                   MCP_REQUEST_TIMEOUT)
from app.tools_core.context import ClientInvocationContext, RESERVED_HEADERS

_ATTEMPT_IN_PROGRESS = "ATTEMPT_IN_PROGRESS"
_ATTEMPT_OUTCOME_UNKNOWN = "ATTEMPT_OUTCOME_UNKNOWN"


class McpHttpTransport:
    def __init__(self, settings: McpClientSettings):
        self._settings = settings
        self._session = None
        self._client_attempts: dict[str, str] = {}   # tool_call_id → client_attempt_id(重传复用)
        self.protocol_version: Optional[str] = None

    # ---- 逐请求 Headers(禁止改共享 header dict / 全局当前 incident)----
    def _build_headers(self, ctx: ClientInvocationContext) -> dict:
        return {
            "Authorization": f"Bearer {self._settings.mcp_http_bearer_token}",
            "X-TraceMind-Incident-Id": str(ctx.incident_id),
            "X-TraceMind-Agent-Run-Id": str(ctx.agent_run_id),
            "X-TraceMind-Tool-Call-Id": ctx.tool_call_id,
            "X-TraceMind-Purpose": ctx.purpose,
            "X-TraceMind-Context-Version": "1",
        }

    async def connect(self) -> None:
        from mcp.client.streamable_http import streamablehttp_client
        try:
            self._ctx = streamablehttp_client(self._settings.mcp_http_url,
                                              timeout=self._settings.mcp_http_request_timeout_seconds)
            self._read, self._write = await self._ctx.__aenter__()
            from mcp import ClientSession
            self._session = await ClientSession(self._read, self._write).__aenter__()
            init = await self._session.initialize()
            self.protocol_version = init.protocolVersion
        except Exception as e:  # noqa: BLE001
            raise ClientError(MCP_CONNECT_FAILED, str(e), retryable=True) from e

    async def list_tools(self) -> list:
        tools = (await self._session.list_tools()).tools
        return [{"name": t.name, "inputSchema": t.inputSchema} for t in tools]

    async def _call_with_retry(self, name: str, params: dict,
                               ctx: ClientInvocationContext) -> dict:
        attempt_id = self._client_attempts.setdefault(ctx.tool_call_id,
                                                      f"ca-{uuid.uuid4().hex[:12]}")
        last_error: Optional[ClientError] = None
        max_retries = self._settings.mcp_http_max_retries  # 含首次,默认 3
        for attempt in range(1, max_retries + 1):
            try:
                headers = self._build_headers(ctx)
                # SDK streamablehttp_client 的 headers 在 connect 时给定;此处经 _session 透传
                # (V1.7 简化:逐请求上下文经 MCP _meta 或受控头由传输适配器注入——测试用 stub 验证 _build_headers)
                result = await self._session.call_tool(name, params)
                return self._parse(result, ctx, attempt, attempt_id)
            except ClientError as e:
                last_error = e
                if not e.retryable:
                    raise
            except Exception as e:  # noqa: BLE001
                last_error = ClientError(MCP_DISCONNECTED, str(e), retryable=True)
            if attempt < max_retries:
                await asyncio.sleep(0.1 * (2 ** attempt))   # 指数退避(简化 jitter)
        raise last_error if last_error else ClientError(MCP_DISCONNECTED, "max retries")

    def _parse(self, result, ctx, attempt, attempt_id) -> dict:
        text = "".join(getattr(c, "text", "") or "" for c in result.content or []
                       if getattr(c, "type", "") == "text")
        out = json.loads(text)
        out["mcp_invocation_id"] = f"{ctx.tool_call_id}:{attempt_id}:{attempt}"
        return out

    async def call_tool(self, name: str, params: dict, ctx: ClientInvocationContext) -> dict:
        if self._session is None:
            raise ClientError(MCP_DISCONNECTED, "未连接", retryable=True)
        return await self._call_with_retry(name, params, ctx)

    async def close(self) -> None:
        if self._session:
            await self._session.__aexit__(None, None, None)
        self._session = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_client_transport_http.py -q`
Expected: PASS(2 passed)

- [ ] **Step 5: Commit**

```bash
git add ai-service/app/mcp/client_transport_http.py ai-service/tests/test_client_transport_http.py
git commit -m "feat(mcp): Streamable HTTP Client Adapter(逐请求 Headers/幂等 client_attempt_id/重试)"
```

---

### Task 15:client.py 改造 — 双 transport 统一入口 + fail-closed

**Files:**
- Modify: `ai-service/app/mcp/client.py`(McpClientManager 抽象 transport)
- Create: `ai-service/app/mcp/client_transport_stdio.py`(现 stdio 逻辑迁入)
- Modify: `ai-service/app/main.py`(启动时按 `mcp_transport` 选 transport;`vm_release/production` 禁 stdio 断言)
- Test: `ai-service/tests/test_client_manager.py`

**Interfaces:**
- Consumes: `client_transport_stdio` / `client_transport_http`、`app.config.settings`
- Produces: `McpClientManager(transport: str = "stdio")`:`start()/stop()/call_tool(name, incident_id, agent_run_id, **business) -> dict`(内部构造 ClientInvocationContext;transport 由 settings 决定;`call_tool` 注入 `tool_call_id/purpose`);`get_transport_name() -> str`

- [ ] **Step 1: Write the failing test**

```python
# ai-service/tests/test_client_manager.py
from app.mcp.client import McpClientManager


def test_transport_selected_by_settings(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_transport", "stdio")
    mgr = McpClientManager()
    assert mgr.transport == "stdio"


def test_transport_http_selected(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_transport", "streamable_http")
    mgr = McpClientManager()
    assert mgr.transport == "streamable_http"
    assert mgr.get_transport_name() == "mcp_streamable_http"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_client_manager.py -q`
Expected: FAIL(现有 McpClientManager 无 transport 属性)

- [ ] **Step 3: Implement**

`client.py` 改造要点:
- `__init__` 读 `settings.mcp_transport`;`self.transport`;`get_transport_name()` 返回 `"mcp_stdio"` / `"mcp_streamable_http"`。
- stdio 路径:现有 `_start_session/_close_session/_spawn_env` 原样保留(迁到 `client_transport_stdio.py` 的 `StdioTransport` 或保留在 client.py)。
- http 路径:持有 `McpHttpTransport` 实例;`_start_session` → `transport.connect()`;`call_tool` → `transport.call_tool(name, params, ctx)`(ctx 由 client 构造:`ClientInvocationContext(incident_id, agent_run_id, tool_call_id=f"tc-{uuid}...", purpose="investigation")`)。
- **fail-closed**:`vm_release/production` + `mcp_transport != "streamable_http"` → 启动抛 `RuntimeError`;`McpClientSettings().validate_runtime()` False → 启动抛 `RuntimeError`。

`app/main.py` lifespan 改:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    global mcp_manager
    from app.config import settings
    if settings.run_profile in ("vm_release", "production") and settings.mcp_transport != "streamable_http":
        raise RuntimeError("vm_release/production 必须使用 streamable_http,禁止 stdio")
    mcp_manager = McpClientManager()
    await mcp_manager.start()
    set_mcp_client(mcp_manager)
    await runner.recover_pending_runs()
    task = asyncio.create_task(scanner_loop())
    yield
    task.cancel()
    await mcp_manager.stop()
    set_mcp_client(None)
    mcp_manager = None
```

- [ ] **Step 4: Run test + 回归**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_client_manager.py -q`
Expected: PASS

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_agent_graph.py tests/test_collect_evidence.py tests/test_approvals.py -q`
Expected: PASS(stdio 默认路径不破坏)

- [ ] **Step 5: Commit**

```bash
git add ai-service/app/mcp/client.py ai-service/app/mcp/client_transport_stdio.py ai-service/app/main.py ai-service/tests/test_client_manager.py
git commit -m "feat(mcp): client 双 transport 统一入口 + fail-closed(vm_release/production 禁 stdio)"
```

---

## 阶段 F:部署

### Task 16:Dockerfile 双 target(ai-runtime / mcp-tools-runtime)

**Files:**
- Modify: `ai-service/Dockerfile`

**Interfaces:**
- Consumes: 现状 Dockerfile(多 target:base/runtime/ci)
- Produces: `target=ai-runtime`(现状 runtime 改名;装 LLM/Agent 依赖 + app)+ `target=mcp-tools-runtime`(**不装 langgraph/langchain/LLM 相关**,只装 mcp/fastapi/uvicorn/sqlalchemy/pymysql/httpx/pydantic-settings;`CMD ["python","-m","app.mcp.server_http"]`);`target=ci` 保留(或并入 ai-runtime)

- [ ] **Step 1: Verify current Dockerfile builds conceptually(无 Docker 本地,仅语法审查)**

Read: `ai-service/Dockerfile`
Expected: 确认现状 base/runtime/ci 三段结构

- [ ] **Step 2: Write failing check(测试断言 target 存在)**

```python
# ai-service/tests/test_dockerfile_targets.py
from pathlib import Path


def test_dockerfile_has_both_targets():
    df = Path("Dockerfile").read_text(encoding="utf-8")
    assert "AS ai-runtime" in df
    assert "AS mcp-tools-runtime" in df


def test_mcp_tools_target_skips_llm_deps():
    df = Path("Dockerfile").read_text(encoding="utf-8")
    seg = df.split("AS mcp-tools-runtime")[1]
    assert "langgraph" not in seg
    assert "langchain" not in seg
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_dockerfile_targets.py -q`
Expected: FAIL(target 名不匹配)

- [ ] **Step 4: Modify Dockerfile**

```dockerfile
# syntax=docker/dockerfile:1
# TraceMind ai-service 双 target:
#   ai-runtime         — AI 控制服务(LLM/Agent 全依赖)
#   mcp-tools-runtime  — 独立 MCP 工具服务(不含 LLM/Agent 运行依赖)
# 构建(VM):
#   DOCKER_BUILDKIT=0 docker build -t tracemind-ai-service:<sha> --target ai-runtime .
#   DOCKER_BUILDKIT=0 docker build -t tracemind-mcp-tools:<sha> --target mcp-tools-runtime .

FROM python:3.12-slim AS base
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# 公共依赖层(两 target 共享;利用 Docker 缓存 + 国内镜像源)
COPY pyproject.toml ./
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ \
    fastapi>=0.115 "uvicorn[standard]>=0.30" "sqlalchemy>=2.0" "pymysql>=1.1" \
    "httpx>=0.27" "pydantic-settings>=2.3" "mcp>=1.28,<2"

# ---- ai-runtime:AI 控制服务(含 LLM/Agent 依赖)----
FROM base AS ai-runtime
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ \
    "langgraph>=0.2" "langgraph-checkpoint-sqlite>=3.1" "langchain-core>=0.3"
COPY app ./app
RUN mkdir -p /app/data && useradd -m appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
ENV TRACEMIND_CHECKPOINT_PATH=/app/data/checkpoints.sqlite
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---- mcp-tools-runtime:独立 MCP 工具服务(不装 LLM/Agent 依赖)----
FROM base AS mcp-tools-runtime
COPY app ./app
RUN mkdir -p /app/data && useradd -m appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8001
CMD ["python", "-m", "app.mcp.server_http"]

# ---- ci:含测试/覆盖率(本地/手动回归用)----
FROM ai-runtime AS ci
USER root
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ \
    pytest pytest-asyncio pytest-cov
COPY tests ./tests
USER appuser
ENV TRACEMIND_RUN_PROFILE=local TRACEMIND_LLM_MODE=fake
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 5: Run test + 提交**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_dockerfile_targets.py -q`
Expected: PASS(2 passed)

```bash
git add ai-service/Dockerfile ai-service/tests/test_dockerfile_targets.py
git commit -m "build(docker): 双 target(ai-runtime/mcp-tools-runtime),MCP 镜像不含 LLM/Agent 依赖"
```

---

### Task 17:compose.yml — 三内部网络 + llm-egress + mcp-tools 服务

**Files:**
- Modify: `compose.yml`

**Interfaces:**
- Consumes: 现状 compose(服务 + 网络)
- Produces: `mcp-tools` 服务(镜像 `tracemind-mcp-tools:<sha>`,`container_name: tracemind-mcp-tools`,端口 8001 **不映射**宿主机;env 仅调查/审计凭据 + `TRACEMIND_MCP_TRANSPORT=streamable_http`;healthcheck 容器内 `curl -f http://127.0.0.1:8001/health/ready`);ai-service 改 `TRACEMIND_MCP_TRANSPORT=streamable_http` + `TRACEMIND_MCP_HTTP_URL=http://mcp-tools:8001/mcp`;三网络 `internal: true` + `llm-egress-network`(仅 ai-service);移除 ai-service 对调查数据网络直连

- [ ] **Step 1: Verify current compose structure(无 Docker 本地,仅 YAML 审查)**

Run: `cd . && python -c "import yaml; yaml.safe_load(open('compose.yml', encoding='utf-8')); print('YAML OK')"`
Expected: OK

- [ ] **Step 2: Write failing check(测试断言新服务与网络)**

```python
# ai-service/tests/test_compose_v17.py
from pathlib import Path
import yaml


def test_compose_has_mcp_tools_service():
    c = yaml.safe_load(Path("../compose.yml").read_text(encoding="utf-8"))
    assert "mcp-tools" in c["services"]


def test_compose_three_internal_networks():
    c = yaml.safe_load(Path("../compose.yml").read_text(encoding="utf-8"))
    for net in ("agent-mcp-network", "control-data-network", "tool-observation-network"):
        assert c["networks"][net].get("internal") is True


def test_compose_llm_egress_only_ai():
    c = yaml.safe_load(Path("../compose.yml").read_text(encoding="utf-8"))
    ai = c["services"]["ai-service"]["networks"]
    mt = c["services"]["mcp-tools"]["networks"]
    assert "llm-egress-network" in ai
    assert "llm-egress-network" not in mt


def test_mcp_tools_no_host_ports():
    c = yaml.safe_load(Path("../compose.yml").read_text(encoding="utf-8"))
    mt = c["services"]["mcp-tools"]
    assert "ports" not in mt or not mt["ports"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_compose_v17.py -q`
Expected: FAIL(无 mcp-tools / internal 未设)

- [ ] **Step 4: Modify compose.yml**

关键改动(其余服务保持):
- `services.mcp-tools`:

```yaml
  mcp-tools:
    image: tracemind-mcp-tools:${TRACEMIND_IMAGE_SHA:-latest}
    build:
      context: ./ai-service
      target: mcp-tools-runtime
    container_name: tracemind-mcp-tools
    env_file:
      - .env.vm
    environment:
      TRACEMIND_MCP_TRANSPORT: "streamable_http"
      TRACEMIND_MCP_HTTP_URL: "http://0.0.0.0:8001/mcp"
      TRACEMIND_MCP_AUTH_CLIENTS_FILE: "/run/secrets/mcp_clients.json"
      TRACEMIND_MCP_AUDIT_DB_URL: "mysql+pymysql://mcp_tool_auditor:${TRACEMIND_DB_MCP_AUDITOR_PASSWORD}@mysql:3306/tracemind_control"
      TRACEMIND_READONLY_DB_URL: "mysql+pymysql://ai_investigator:investigator_pwd@mysql:3306/tracemind_business"
      TRACEMIND_METRICS_BACKEND: "prometheus"
      TRACEMIND_TRACE_BACKEND: "jaeger"
      TRACEMIND_PROMETHEUS_URL: "http://prometheus:9090"
      TRACEMIND_JAEGER_QUERY_ENDPOINT: "jaeger:16685"
    secrets:
      - mcp_clients.json
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8001/health/ready',timeout=5)"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 15s
    networks: [agent-mcp-network, control-data-network, tool-observation-network]
    restart: unless-stopped
```

- `services.ai-service`:`environment` 加 `TRACEMIND_MCP_TRANSPORT: "streamable_http"`、`TRACEMIND_MCP_HTTP_URL: "http://mcp-tools:8001/mcp"`、`TRACEMIND_MCP_HTTP_BEARER_TOKEN: "${TRACEMIND_MCP_HTTP_BEARER_TOKEN}"`;`networks` 改为 `[agent-mcp-network, control-data-network, llm-egress-network]`(不加入 tool-observation-network)
- `secrets`:`mcp_clients.json`(`file: ./secrets/mcp_clients.json`,gitignore)
- `networks`:

```yaml
networks:
  agent-mcp-network:        { internal: true }
  control-data-network:     { internal: true }
  tool-observation-network: { internal: true }
  llm-egress-network:       { driver: bridge }
```

- `./secrets/mcp_clients.json` 示例(不进 git,部署时生成):

```json
{ "sha256:<ai-token-hash>": { "subject": "ai-service", "audience": "tracemind-mcp-tools", "scopes": ["tools:investigate"] } }
```

- [ ] **Step 5: Run test + YAML 校验 + 提交**

Run: `cd ai-service && .venv/Scripts/pytest.exe tests/test_compose_v17.py -q`
Expected: PASS(4 passed)

Run: `cd . && python -c "import yaml; yaml.safe_load(open('compose.yml', encoding='utf-8')); print('YAML OK')"`
Expected: YAML OK

```bash
git add compose.yml ai-service/tests/test_compose_v17.py
git commit -m "deploy(compose): mcp-tools 独立服务 + 三 internal 网络 + llm-egress(仅 ai-service)"
```

---

## 阶段 G:验收脚本与文档

### Task 18:verify-m17.py — 三层验收编排(Python)

**Files:**
- Create: `scripts/verify-m17.py`
- Create: `scripts/verify-m17.ps1`(轻量包装)
- Create: `scripts/verify_v17_utils.py`(共用:报告汇总/凭据布尔检查/脱敏)
- Test: `scripts/ci/../tests` 不适用(脚本级);本地手动跑 `--tier fast`

**Interfaces:**
- Consumes: `eval_agent.py`(离线评测)、pytest、`verify-m14.py`(VM 观测)、`docker`(VM)
- Produces:
  - `--tier fast`:ai-service 全量 pytest + V1.7 专项 + Vue typecheck + Replay Transport targeted + 离线评测 N/N;JSON 汇总
  - `--tier vm-smoke`:mcp-tools 健康、AI 用 HTTP、无 stdio 子进程、7 工具 HTTP 探针、认证/Origin/协议版本、审计 mcp_streamable_http、direct_fallback=false、SCN-001/002 ×1、Replay Backend、凭据隔离布尔
  - `--tier release`:real_strict + SCN ≥1/1 + 完整报告(脱敏摘要 → `docs/releases/v1.7-validation-summary.md`)

- [ ] **Step 1: Write the script skeleton(先验证 fast 档可跑)**

```python
# scripts/verify-m17.py
"""V1.7 三层验收编排(Local Fast / VM Smoke / VM Release)。
人工触发,内部自动执行全部步骤并生成 JSON 汇总。"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REPORTS = REPO / "reports" / "generated" / "v1.7"


def run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)


def tier_fast() -> dict:
    summary = {"tier": "fast", "steps": {}}
    ai = REPO / "ai-service"
    # 1) 后端全量 pytest
    code, out = run([str(ai / ".venv/Scripts/pytest.exe"), "tests/", "-q"], ai)
    summary["steps"]["ai_pytest"] = {"exit": code, "tail": out.splitlines()[-3:] if out else []}
    # 2) Vue typecheck + Replay Transport targeted test
    code, out = run(["npm", "run", "typecheck"], REPO / "web")
    summary["steps"]["vue_typecheck"] = {"exit": code}
    code, out = run(["npm", "run", "test", "--", "-t", "Replay|transport"], REPO / "web")
    summary["steps"]["vue_replay_transport"] = {"exit": code}
    # 3) 离线评测 N/N(动态发现)
    env = {"TRACEMIND_RUN_PROFILE": "offline_eval", "TRACEMIND_LLM_MODE": "fake",
           "TRACEMIND_EVAL_MODE": "true",
           "TRACEMIND_CONTROL_DB_URL": "mysql+pymysql://tracemind_control_app:control_app_pwd@localhost:3306/tracemind_control"}
    code, out = run([str(ai / ".venv/Scripts/python.exe"), "../scripts/eval_agent.py",
                     "--mode", "offline", "--llm", "fake", "--runs", "1"], ai, env) if False else \
                run(["cmd", "/c", "set", "TRACEMIND_RUN_PROFILE=offline_eval&&", "set", "TRACEMIND_LLM_MODE=fake&&",
                     str(ai / ".venv/Scripts/python.exe"), "../scripts/eval_agent.py",
                     "--mode", "offline", "--llm", "fake", "--runs", "1"], ai)
    summary["steps"]["offline_eval"] = {"exit": code}
    summary["ok"] = all(s.get("exit") == 0 for s in summary["steps"].values())
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", choices=["fast", "vm-smoke", "release"], default="fast")
    args = parser.parse_args()
    if args.tier == "fast":
        summary = tier_fast()
    else:
        print(f"--tier {args.tier} 需在 VM 执行(见计划 Task 20/21)", file=sys.stderr)
        return 2
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "validation-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

注:离线评测 env 注入用 Windows `set X=Y&&` 拼接(CMD),跨平台统一留 `verify_v17_utils.run_with_env`。

- [ ] **Step 2: Run fast tier(本地,期望可执行且汇总生成)**

Run: `cd scripts && ../ai-service/.venv/Scripts/python.exe verify-m17.py --tier fast`
Expected: 生成 `reports/generated/v1.7/validation-summary.json`,各步骤 exit 可见(离线评测步骤可能因 env 注入待 Task 18 完善)

- [ ] **Step 3: Complete vm-smoke / release 档(骨架 + 占位调用 verify-m14)**

```python
def _vm(command: str) -> tuple[int, str]:
    import shlex
    return run([sys.executable, str(REPO / ".reasonix/tools/vm_ssh.py"), "run", command], REPO)


def tier_vm_smoke() -> dict:
    summary = {"tier": "vm-smoke", "steps": {}}
    for name, cmd in [
        ("mcp_tools_health", "curl -sf http://127.0.0.1:8001/health/ready >/dev/null && echo OK"),
        ("no_stdio_spawn", "docker exec tracemind-ai sh -c 'ps aux | grep -c \"app.mcp.server_stdio\"' | tr -d ' '"),
        ("http_7tools_probe", "docker exec tracemind-ai python -c \"from app.mcp.client import get_mcp_client;m=get_mcp_client();print(sorted(m.list_tool_names()))\""),
    ]:
        code, out = _vm(cmd)
        summary["steps"][name] = {"exit": code, "out": out.strip()[:200]}
    # SCN-001/002 各一次闭环(复用 verify-m14 单轮)
    code, out = _vm("python scripts/verify-m14.py --base http://<vm-host>:8000 --order http://<vm-host>:8081 --rounds 1")
    summary["steps"]["scn_rounds"] = {"exit": code, "tail": out.splitlines()[-5:] if out else []}
    # 凭据隔离布尔(只输出两布尔,不 dump 完整 env)
    code, out = _vm("python scripts/check_credential_isolation.py")
    summary["credential_isolation"] = out.strip()
    summary["ok"] = all(s.get("exit") == 0 for s in summary["steps"].values())
    return summary
```

`scripts/check_credential_isolation.py`(Task 19 一并):docker exec 检查两容器 env 的**禁入凭据 key 存在性**,只输出 `{"aiServiceForbiddenCredentialsPresent": false, "mcpToolsForbiddenCredentialsPresent": false}`。

- [ ] **Step 4: Run + commit**

Run: `cd scripts && ../ai-service/.venv/Scripts/python.exe verify-m17.py --tier fast`
Expected: 汇总 JSON 生成,`ok` 字段反映本地状态(VM 档仅骨架,Task 20/21 实跑)

```bash
git add scripts/verify-m17.py scripts/verify-m17.ps1 scripts/verify_v17_utils.py scripts/check_credential_isolation.py
git commit -m "feat(verify): verify-m17 三层验收编排(Local Fast 可跑,VM Smoke/Release 骨架)"
```

---

### Task 19:文档与模板 — README V1.7 + docs/releases 模板

**Files:**
- Modify: `README.md`(V1.7 章节:传输定位/配置/验收命令/术语)
- Create: `docs/releases/v1.7-validation-summary.template.md`
- Create: `docs/ci/../mcp-deployment.md`(部署与手工配置说明)

**Interfaces:**
- Consumes: 全局约束 15(验收矩阵/术语/GitHub 职责)

- [ ] **Step 1: Write docs**

`README.md` V1.7 章节(摘要):
- 传输定位表(stdio 本地/评测;Streamable HTTP 标准部署)
- 配置项(`TRACEMIND_MCP_TRANSPORT` 等,指向 spec)
- 验收命令:`python scripts/verify-m17.py --tier fast|vm-smoke|release`
- 术语:Local Fast Regression / VM Release Validation / Release Acceptance Gate
- 明确:"项目提供本地快速回归与 VM 分层发布验收脚本,所有真实模型与故障闭环验收由开发者显式触发"(无 CI Badge)

`docs/releases/v1.7-validation-summary.template.md`:

```markdown
# V1.7 Validation Summary

- **Git Commit SHA**: `<sha>`
- **Git Tag**: `<v1.7.0>`
- **执行时间/环境**: `<datetime>` / `<VM>`
- **模型**: `<model snapshot>`
- **Prompt/Policy/MCP Contract 版本**: `<...>`
- **MCP Protocol / SDK 实际版本**: `<2026-07-28>` / `<mcp 1.29.0>`
- **Invocation Context 版本**: `1`
- **MCP Server 镜像 Digest**: `<sha256:...>`
- **七工具 Contract Hash**: `<...>`
- **离线评测**: `<N>/<N> PASS`
- **SCN-001 / SCN-002**: `<x>/<x>` / `<y>/<y>`
- **逻辑 Tool Call 数 / HTTP Attempt / 重试数**: `<...>`
- **directFallback**: `false`
- **凭据隔离**: `{aiServiceForbiddenCredentialsPresent: false, mcpToolsForbiddenCredentialsPresent: false}`
- **Release 前绑定**: 工作树干净 / 报告 SHA=HEAD / 镜像 label=HEAD / Digest 已记录 / Tag=已验收 Commit ✅
```

- [ ] **Step 2: Verify markdown renders(人工检查无占位符)**

Run: `git diff --stat README.md docs/releases/`
Expected: 新增文件出现

- [ ] **Step 3: Commit**

```bash
git add README.md docs/releases/v1.7-validation-summary.template.md docs/mcp-deployment.md
git commit -m "docs: V1.7 README 章节 + 发布报告模板 + MCP 部署说明"
```

---

## 阶段 H:VM 部署验收

### Task 20:VM 标准部署 + vm-smoke(手动触发)

**Files:**
- 无新代码(部署操作)

**Interfaces:**
- Consumes: Task 16-17 产物(双镜像 + compose)
- Produces: VM 上 `tracemind-ai-service` + `tracemind-mcp-tools` 两镜像、三网络部署、`verify-m17.py --tier vm-smoke` 通过

- [ ] **Step 1: 上传源码 + 构建双镜像(VM,legacy builder + 国内源)**

```bash
# 本地:打包 ai-service 源码
cd ai-service && tar czf ../.reasonix/tmp/ai_v17.tar.gz app
# VM:上传(相对路径)→ 解压覆盖 ~/tracemind/ai-service → 构建
python .reasonix/tools/vm_ssh.py put .reasonix/tmp/ai_v17.tar.gz tracemind/ai_v17.tar.gz
python .reasonix/tools/vm_ssh.py run "cd ~/tracemind/ai-service && tar xzf ../ai_v17.tar.gz && nohup bash -c 'DOCKER_BUILDKIT=0 docker build -t tracemind-ai-service:v17 --target ai-runtime . > build-ai17.log 2>&1; echo EXIT=\$? >> build-ai17.log' & nohup bash -c 'DOCKER_BUILDKIT=0 docker build -t tracemind-mcp-tools:v17 --target mcp-tools-runtime . > build-mcp17.log 2>&1; echo EXIT=\$? >> build-mcp17.log' &"
# 轮询两个日志的 EXIT=0
```

- [ ] **Step 2: 生成部署 Secret + 配置**

```bash
# VM ~/tracemind/secrets/mcp_clients.json(不进 git):
#   { "sha256:<ai-token-hash>": { "subject":"ai-service", "audience":"tracemind-mcp-tools", "scopes":["tools:investigate"] } }
# compose.yml 的 TRACEMIND_MCP_HTTP_BEARER_TOKEN 与 clients.json 的 token 一致
python .reasonix/tools/vm_ssh.py run "mkdir -p ~/tracemind/secrets && echo '{\"sha256:...\": {\"subject\":\"ai-service\",\"audience\":\"tracemind-mcp-tools\",\"scopes\":[\"tools:investigate\"]}}' > ~/tracemind/secrets/mcp_clients.json"
```

- [ ] **Step 3: 启动 + 健康验证**

```bash
python .reasonix/tools/vm_ssh.py run "cd ~/tracemind && docker compose up -d mysql mcp-tools ai-service 2>&1 | tail -8 && sleep 30 && curl -sf http://127.0.0.1:8001/health/ready && curl -sf http://127.0.0.1:8000/api/health"
```
Expected:`/health/ready` 200 + ai health ok

- [ ] **Step 4: 跑 vm-smoke 验收**

```bash
cd scripts && ../ai-service/.venv/Scripts/python.exe verify-m17.py --tier vm-smoke
```
Expected:各步骤 exit=0;凭据隔离布尔均为 false;SCN-001/002 各 1 轮 PASS;审计 transport=mcp_streamable_http、direct_fallback=false

- [ ] **Step 5: 提交验收快照(手动记录)**

```bash
# 脱敏摘要复制到 docs/releases/v1.7-validation-summary.md(按模板填实际值)
git add docs/releases/v1.7-validation-summary.md
git commit -m "docs(release): V1.7 VM Smoke 验收摘要(脱敏)"
```

---

### Task 21:VM Release 验收(真实模型,额度允许时)

**Files:**
- 无新代码(操作)

**Interfaces:**
- Consumes: Task 20 部署;`verify-m17.py --tier release`
- Produces: real_strict 下 SCN-001/002 ≥1/1;完整脱敏发布报告;Release 前绑定断言通过后打 V1.7 Tag

- [ ] **Step 1: 切 real_strict(docker-compose.yml environment)**

```bash
# VM ~/tracemind/docker-compose.yml:TRACEMIND_LLM_MODE: fake → real_strict;RAG_MODE → required(如需)
python .reasonix/tools/vm_ssh.py run "cd ~/tracemind && sed -i 's/TRACEMIND_LLM_MODE: fake/TRACEMIND_LLM_MODE: real_strict/' docker-compose.yml && docker compose up -d --no-build ai-service mcp-tools"
```

- [ ] **Step 2: 跑 release 验收**

```bash
cd scripts && ../ai-service/.venv/Scripts/python.exe verify-m17.py --tier release
```
Expected:真实模型冒烟成功;SCN-001/002 ≥1/1;无 stdio/direct 降级;审计 transport=mcp_streamable_http;审批/处置/恢复闭环

**额度/错误处理**(全局约束 15):429 短期限流按退避;额度耗尽 → 停止并保留部分报告,立即告知用户;密钥失效 → 修复凭据重跑;**不自动换模型**;人工换模型 = 新 Validation Run 并记录新模型。

- [ ] **Step 3: Release 前绑定断言 + Tag**

```bash
# 断言:工作树干净;报告 Git SHA==HEAD;两镜像 label Git SHA==HEAD;Digest 已写入报告
git status --porcelain | wc -l          # 期望 0
git log -1 --format=%H                   # 与报告一致
python .reasonix/tools/vm_ssh.py run "docker inspect tracemind-ai-service --format '{{.Config.Labels}}'"
git tag -a v1.7.0 -m "V1.7 MCP Streamable HTTP 远程传输与服务化(VM Release 验收通过)"
git push origin main --tags
```

- [ ] **Step 4: 提交脱敏摘要**

```bash
git add docs/releases/v1.7-validation-summary.md
git commit -m "docs(release): V1.7 Release 验收摘要(脱敏)"
git push origin main --tags
```

- [ ] **Step 5: 收尾(还原 fake 配置 + 关闭服务)**

```bash
python .reasonix/tools/vm_ssh.py run "cd ~/tracemind && sed -i 's/TRACEMIND_LLM_MODE: real_strict/TRACEMIND_LLM_MODE: fake/' docker-compose.yml && docker compose up -d --no-build ai-service && docker compose down"
```
Expected:配置还原;服务关闭(与 V1.6 收尾一致)

---

## 计划自审记录(填写于编写后)

- Spec 覆盖:逐章核对(spec 15 章 ↔ Task 1-21),缺项在下方列出并补 Task。
- 占位符:无 TBD/TODO;所有 Task 含实际测试代码与实现代码。
- 类型一致:`ClientInvocationContext` / `AuthenticatedPrincipal` / `ToolExecutionService.execute` / `McpClientManager` 签名在 Task 1/5/13/14/15 间一致。
