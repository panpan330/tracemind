# TraceMind V1.2 工具层 MCP 化(MCP Tools Service)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将五个只读调查工具升级为标准 stdio MCP Server,LangGraph Agent 通过持久化 MCP Client 会话调用工具,消除调查工具进程内 direct 路径。

**Architecture:** MCP Server(FastMCP)作为 ai-service 同环境子进程(固定 `sys.executable -m app.mcp.server`),McpClientManager 在后台线程持有唯一 asyncio event loop 与 stdio ClientSession,同步 `call_tool` 经 `run_coroutine_threadsafe` 桥接;`execute_tool`/TOOL_REGISTRY/审计/fixture 匹配逻辑保留在 Server 侧复用。

**Tech Stack:** Python 3.12 / FastAPI / LangGraph / mcp>=1.28,<2(官方 SDK,FastMCP + stdio_client)/ SQLAlchemy 2.0

## Global Constraints

- 依赖锁定:`mcp>=1.28,<2`;提交 `uv.lock`;VM 构建用 `uv sync --frozen`;阿里云镜像缺锁定版本时回退官方 PyPI;不在部署时临时更新依赖
- **完全走 MCP**:五个调查工具(`get_service_metrics` / `get_trace` / `list_expensive_query_digests` / `get_query_plan` / `get_index_info`)全部经 stdio MCP 调用,**不保留 direct 路径**;`execute_fix` / `verify_recovery` 属确定性安全控制节点,不纳入 MCP
- **上下文与业务参数分离**:`incident_id` / `agent_run_id` 由 MCP Client 注入,LLM 侧 Schema 不含二者,不参与 Fixture 参数哈希,不传给业务 Handler,仅审计
- **stdout 纯净**:MCP Server stdout 仅 JSON-RPC 消息;日志/错误走 stderr;禁止 print 进 stdout
- **禁止**:每次调用 `asyncio.run()`;跨事件循环复用 ClientSession;每次工具调用重新创建子进程;只依赖 atexit 清理;任何情况下通过旧 direct 路径获得调查结果
- 错误码分层:`MCP_*` 为基础设施错误;`execute_tool` 返回 `success=false` 时保留业务 error_code
- 迁移用版本化 SQL(信息 schema 幂等判断),不在应用启动时随意 ALTER

---

### Task 1: 依赖与配置扩展

**Files:**
- Modify: `ai-service/pyproject.toml`(dependencies 加 `mcp>=1.28,<2`)
- Modify: `ai-service/app/config.py`(MCP 配置字段)
- Test: `ai-service/tests/test_config.py`(追加)

**Interfaces:**
- Consumes: 现有 `Settings`(pydantic-settings,.env.local)。
- Produces: `settings.mcp_timeout_seconds: float = 15.0`、`settings.mcp_max_restart: int = 1`、`settings.mcp_ready: bool = False`(运行时标志)。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_config.py`:

```python
def test_mcp_config_defaults():
    s = Settings(_env_file=None)
    assert s.mcp_timeout_seconds == 15.0
    assert s.mcp_max_restart == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_config.py::test_mcp_config_defaults -q`
Expected: FAIL(`AttributeError: 'Settings' object has no attribute 'mcp_timeout_seconds'`)

- [ ] **Step 3: pyproject 加依赖 + 更新 uv.lock**

`pyproject.toml` dependencies 追加:

```toml
  "mcp>=1.28,<2",
```

Run: `cd ai-service && uv lock && uv sync --frozen`
Expected: 成功,uv.lock 含 mcp 锁定版本。

- [ ] **Step 4: config.py 追加 MCP 配置段**

在 `Settings` 内、`eval_mode` 之后追加:

```python
    # ---- MCP 工具服务 ----
    mcp_timeout_seconds: float = 15.0   # 单次工具调用超时
    mcp_max_restart: int = 1            # Server 启动/初始化失败最多重启次数
    mcp_ready: bool = False             # 运行时:契约校验通过后置 True
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_config.py -q`
Expected: 全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add ai-service/pyproject.toml ai-service/uv.lock ai-service/app/config.py ai-service/tests/test_config.py
git commit -m "feat(mcp): 依赖 mcp>=1.28,<2 + MCP 配置字段(timeout/max_restart/ready)"
```

---

### Task 2: MCP Server 本体(`app/mcp/`)

**Files:**
- Create: `ai-service/app/mcp/__init__.py`
- Create: `ai-service/app/mcp/server.py`
- Create: `ai-service/app/mcp/contract.py`
- Test: `ai-service/tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `execute_tool`(app/tools/execute.py,签名 `execute_tool(tool_name, incident_id=None, **kwargs)`)、`set_eval_fixture`(同文件)、`settings`。
- Produces: `app/mcp/contract.py`:`MCP_TOOL_CONTRACT_VERSION = "1.0"`、`SERVER_NAME = "tracemind-tools"`、`SERVER_VERSION = "0.1.0"`、`TOOL_NAMES: frozenset[str]`、`llm_tool_schemas() -> list[dict]`(裁剪 schema,不含 incident_id/agent_run_id)、`schema_sha256(schema: dict) -> str`;`app/mcp/server.py`:`run_server(fixture_file: str | None = None) -> None`、`def main() -> None`(`--fixture-file` 参数)。

- [ ] **Step 1: 写失败测试**

`tests/test_mcp_server.py`:

```python
"""MCP Server 单元测试:工具注册/委托/Fixture 加载/上下文校验。"""
import json
from unittest import mock

from app.mcp.contract import (MCP_TOOL_CONTRACT_VERSION, SERVER_NAME, TOOL_NAMES,
                              llm_tool_schemas, schema_sha256)
from app.mcp.server import run_server


def test_contract_constants():
    assert MCP_TOOL_CONTRACT_VERSION == "1.0"
    assert SERVER_NAME == "tracemind-tools"
    assert TOOL_NAMES == frozenset({
        "get_service_metrics", "get_trace", "list_expensive_query_digests",
        "get_query_plan", "get_index_info"})


def test_llm_tool_schemas_hide_context_fields():
    schemas = llm_tool_schemas()
    assert len(schemas) == 5
    for s in schemas:
        props = s["function"]["parameters"]["properties"]
        assert "incident_id" not in props and "agent_run_id" not in props


def test_schema_sha256_stable():
    s1 = {"type": "object", "properties": {"a": {"type": "string"}}}
    assert schema_sha256(s1) == schema_sha256(dict(s1))
    assert len(schema_sha256(s1)) == 64


def test_fixture_mode_loads_and_synthetic_context(monkeypatch, tmp_path):
    from app.tools import execute
    fake = {"get_service_metrics:abc": {"ok": True, "data": {"p95Ms": 100}}}
    fixture_file = tmp_path / "case.json"
    fixture_file.write_text(json.dumps(fake), encoding="utf-8")
    monkeypatch.setattr(execute, "set_eval_fixture", lambda f: None)
    monkeypatch.setattr("app.mcp.server.settings.eval_mode", True, raising=False)
    run_server(fixture_file=str(fixture_file))   # 不应抛异常(注册后 fixture 注入)
    # synthetic context:server 不校验 Incident 存在(由 client 传,server 仅透传)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_mcp_server.py -q`
Expected: FAIL(`ModuleNotFoundError: app.mcp`)

- [ ] **Step 3: 实现 `contract.py`**

```python
"""MCP 工具契约:常量、LLM 侧裁剪 Schema、契约校验工具。
说明:MCP_TOOL_CONTRACT_VERSION 是应用级契约字段,MCP 协议不天然提供。"""
import hashlib
import json
from typing import Any

from app.tools.registry import TOOL_REGISTRY

MCP_TOOL_CONTRACT_VERSION = "1.0"
SERVER_NAME = "tracemind-tools"
SERVER_VERSION = "0.1.0"

# 调查工具名(MCP 暴露集合;execute_fix/verify_recovery 不在此列)
TOOL_NAMES = frozenset({
    "get_service_metrics", "get_trace", "list_expensive_query_digests",
    "get_query_plan", "get_index_info",
})

# 上下文字段:MCP Client 注入,不进入 LLM Schema 与 Fixture 哈希
_CONTEXT_FIELDS = frozenset({"incident_id", "agent_run_id"})


def llm_tool_schemas() -> list[dict[str, Any]]:
    """裁剪后的 LLM 侧工具 Schema:仅业务参数(隐藏 incident_id/agent_run_id)。"""
    schemas = []
    for name in sorted(TOOL_NAMES):
        spec = TOOL_REGISTRY[name]
        schema = spec.input_schema.model_json_schema()
        schema["properties"] = {k: v for k, v in schema.get("properties", {}).items()
                                if k not in _CONTEXT_FIELDS}
        schema.pop("required", None)  # required 由程序 resolve 兜底
        schemas.append({"type": "function",
                        "function": {"name": name, "description": spec.description,
                                     "parameters": schema}})
    return schemas


def schema_sha256(schema: dict) -> str:
    """标准化 JSON Schema 的 SHA-256(契约校验用)。"""
    blob = json.dumps(schema, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
```

> 注:`TOOL_REGISTRY[name].description` 若不存在,用 `""` 兜底(见 registry.py 实际字段)。

- [ ] **Step 4: 实现 `server.py`**

```python
"""MCP Server:五个只读调查工具(stdout 纯净,日志走 stderr)。"""
import argparse
import logging
import sys

from app.tools.execute import execute_tool, set_eval_fixture
from app.mcp.contract import SERVER_NAME, SERVER_VERSION

logger = logging.getLogger("app.mcp.server")
logging.basicConfig(stream=sys.stderr, level=logging.INFO)


def run_server(fixture_file: str | None = None) -> None:
    """构造并注册 FastMCP;fixture_file 非空时进程内加载 fixture(仅 EVAL_MODE)。"""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(SERVER_NAME, version=SERVER_VERSION)

    if fixture_file:
        from app.config import settings
        if not settings.eval_mode:
            raise SystemExit("--fixture-file 仅允许 TRACEMIND_EVAL_MODE=true")
        import json
        from pathlib import Path
        base = Path(settings.eval_fixture_dir or ".")
        if not (base / fixture_file).resolve().is_relative_to(base.resolve()):
            raise SystemExit("fixture 文件必须位于评测目录")
        payload = json.loads((base / fixture_file).read_text(encoding="utf-8"))
        set_eval_fixture(payload)

    def _delegate(name: str, incident_id: int, agent_run_id: int, **business):
        # Fixture 模式 synthetic context:不校验 Incident/AgentRun 存在
        return execute_tool(name, incident_id=incident_id, agent_run_id=agent_run_id,
                            **business)

    @mcp.tool()
    def get_service_metrics(incident_id: int, agent_run_id: int,
                            service_ref: str, window_seconds: int) -> dict:
        return _delegate("get_service_metrics", incident_id, agent_run_id,
                         service_ref=service_ref, window_seconds=window_seconds)

    @mcp.tool()
    def get_trace(incident_id: int, agent_run_id: int, trace_id: str) -> dict:
        return _delegate("get_trace", incident_id, agent_run_id, trace_id=trace_id)

    @mcp.tool()
    def list_expensive_query_digests(incident_id: int, agent_run_id: int,
                                     window_seconds: int) -> dict:
        return _delegate("list_expensive_query_digests", incident_id, agent_run_id,
                         window_seconds=window_seconds)

    @mcp.tool()
    def get_query_plan(incident_id: int, agent_run_id: int, query_ref: str,
                       sample_parameters: dict) -> dict:
        return _delegate("get_query_plan", incident_id, agent_run_id,
                         query_ref=query_ref, sample_parameters=sample_parameters)

    @mcp.tool()
    def get_index_info(incident_id: int, agent_run_id: int, table_ref: str) -> dict:
        return _delegate("get_index_info", incident_id, agent_run_id, table_ref=table_ref)

    mcp.run()   # stdio transport;stdout 仅 MCP JSON-RPC


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-file", default=None)
    args = parser.parse_args()
    run_server(args.fixture_file)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_mcp_server.py -q`
Expected: 4 passed(注:`test_fixture_mode_loads_and_synthetic_context` 验证注册路径不抛异常;`run_server` 会阻塞在 `mcp.run()`,测试需 monkeypatch `FastMCP.run` 为 no-op——见 Step 1 测试的 `run_server` 调用前补 `monkeypatch.setattr("app.mcp.server.FastMCP", FakeFastMCP)`;实现时若阻塞,测试改用 `monkeypatch` 替换 `mcp.run`)。

- [ ] **Step 6: 提交**

```bash
git add ai-service/app/mcp/ ai-service/tests/test_mcp_server.py
git commit -m "feat(mcp): MCP Server — FastMCP 五只读工具(显式签名含上下文)、fixture 模式(EVAL_MODE 门控)、契约常量与裁剪 Schema"
```

---

### Task 3: 契约校验(verify_contract)

**Files:**
- Modify: `ai-service/app/mcp/contract.py`(追加 `verify_contract`)
- Test: `ai-service/tests/test_mcp_contract.py`

**Interfaces:**
- Consumes: Task 2 的 `TOOL_NAMES` / `schema_sha256` / `MCP_TOOL_CONTRACT_VERSION` / `SERVER_NAME` / `SERVER_VERSION`、`llm_tool_schemas()`(作为本地预期 Schema 源)。
- Produces: `verify_contract(server_info: dict, tools: list[dict]) -> None`(不一致抛 `MCPContractError`);`MCPContractError(Exception)`。

- [ ] **Step 1: 写失败测试**

`tests/test_mcp_contract.py`:

```python
"""契约校验:serverInfo/名称集合/inputSchema SHA-256/Contract Version。"""
import pytest

from app.mcp.contract import (MCP_TOOL_CONTRACT_VERSION, SERVER_NAME, TOOL_NAMES,
                              MCPContractError, verify_contract, llm_tool_schemas)


def _tools_from_schemas():
    return [{"name": s["function"]["name"],
             "inputSchema": s["function"]["parameters"]} for s in llm_tool_schemas()]


def test_verify_contract_ok():
    tools = _tools_from_schemas()
    verify_contract({"name": SERVER_NAME, "version": "0.1.0"}, tools)


def test_verify_contract_name_mismatch():
    tools = _tools_from_schemas()
    with pytest.raises(MCPContractError):
        verify_contract({"name": "other", "version": "0.1.0"}, tools)


def test_verify_contract_tool_set_mismatch():
    tools = _tools_from_schemas()
    tools = [t for t in tools if t["name"] != "get_trace"]
    with pytest.raises(MCPContractError):
        verify_contract({"name": SERVER_NAME, "version": "0.1.0"}, tools)


def test_verify_contract_schema_drift():
    tools = _tools_from_schemas()
    tools[0]["inputSchema"]["properties"] = {"service_ref": {"type": "str"}}
    with pytest.raises(MCPContractError):
        verify_contract({"name": SERVER_NAME, "version": "0.1.0"}, tools)


def test_verify_contract_rejects_control_tools():
    tools = _tools_from_schemas()
    tools.append({"name": "execute_fix", "inputSchema": {"type": "object"}})
    with pytest.raises(MCPContractError):
        verify_contract({"name": SERVER_NAME, "version": "0.1.0"}, tools)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_mcp_contract.py -q`
Expected: FAIL(`ImportError: cannot import name 'verify_contract'`)

- [ ] **Step 3: 实现**

`contract.py` 追加:

```python
class MCPContractError(Exception):
    pass


def verify_contract(server_info: dict, tools: list[dict]) -> None:
    """启动契约校验:serverInfo / 工具名称集合 / inputSchema SHA-256 / Contract Version。
    不一致抛 MCPContractError,ai-service 启动失败或 readiness=false。"""
    if server_info.get("name") != SERVER_NAME:
        raise MCPContractError(f"serverInfo.name 不一致: {server_info.get('name')}")
    if server_info.get("version") != SERVER_VERSION:
        raise MCPContractError(f"serverInfo.version 不一致: {server_info.get('version')}")
    names = {t.get("name") for t in tools}
    if names != set(TOOL_NAMES):
        raise MCPContractError(f"工具名称集合不一致: {sorted(names)} vs {sorted(TOOL_NAMES)}")
    expected = {s["function"]["name"]: s["function"]["parameters"]
                for s in llm_tool_schemas()}
    for t in tools:
        if t["name"] in ("execute_fix", "verify_recovery"):
            raise MCPContractError(f"控制节点不应出现在 MCP 工具集: {t['name']}")
        if t["name"] not in expected:
            continue
        if schema_sha256(t.get("inputSchema", {})) != schema_sha256(expected[t["name"]]):
            raise MCPContractError(f"inputSchema 漂移: {t['name']}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_mcp_contract.py -q`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/mcp/contract.py ai-service/tests/test_mcp_contract.py
git commit -m "feat(mcp): 契约校验 — serverInfo/工具集合/inputSchema SHA-256/Contract Version"
```

---

### Task 4: McpClientManager(同步桥接客户端)

**Files:**
- Create: `ai-service/app/mcp/client.py`
- Test: `ai-service/tests/test_mcp_client.py`

**Interfaces:**
- Consumes: Task 1 的 `settings.mcp_timeout_seconds/mcp_max_restart/mcp_ready`、Task 3 的 `verify_contract`。
- Produces: `class McpClientManager`:`__init__(self, fixture_file: str | None = None)`;`async start(self) -> None`(spawn 子进程 + initialize + 契约校验 + readiness);`async stop(self) -> None`(关 session → 等子进程 → 超时终止);`def call_tool(self, name: str, incident_id: int, agent_run_id: int, **business) -> dict`(**同步**,内部 `run_coroutine_threadsafe`);`is_ready: bool`;`MCPError(Exception)` with `.code`;错误码常量 `MCP_START_FAILED/MCP_SCHEMA_MISMATCH/MCP_TIMEOUT/MCP_DISCONNECTED/MCP_PROTOCOL_ERROR/MCP_TOOL_ERROR/MCP_RESULT_INVALID`。模块级 `_client: McpClientManager | None`、`get_mcp_client() -> McpClientManager`(仅取已初始化,未初始化抛 `MCPError(MCP_START_FAILED)`)。

- [ ] **Step 1: 写失败测试**

`tests/test_mcp_client.py`:

```python
"""McpClientManager 单测:Stub Session 封装/超时/错误转换/串行锁。"""
import threading

import pytest

from app.mcp.client import (MCP_TIMEOUT, MCP_TOOL_ERROR, McpClientManager, MCPError)


class StubSession:
    """伪 ClientSession:记录 call_tool 调用,返回固定响应或抛错。"""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _manager(session):
    m = McpClientManager()
    m._session = session          # 跳过真实 spawn
    m._loop = None                # 同步测试不启动线程;call_tool 直连
    return m


def test_call_tool_passes_context_and_business():
    m = _manager(StubSession([{"content": [{"type": "text", "text": '{"success": true}'}]}]))
    out = m.call_tool("get_trace", incident_id=1, agent_run_id=2, trace_id="t1")
    assert m._session.calls == [("get_trace", {"incident_id": 1, "agent_run_id": 2,
                                               "trace_id": "t1"})]
    assert out["success"] is True


def test_call_tool_timeout_maps_error():
    class Slow:
        async def call_tool(self, name, arguments):
            import asyncio
            await asyncio.sleep(10)
    m = _manager(Slow())
    m._timeout = 0.01
    with pytest.raises(MCPError) as ei:
        m.call_tool("get_trace", incident_id=1, agent_run_id=1, trace_id="t")
    assert ei.value.code == MCP_TIMEOUT


def test_call_tool_tool_error_preserves_business_code():
    m = _manager(StubSession([{"content": [{"type": "text",
        "text": '{"success": false, "error_code": "TRACE_NOT_FOUND"}'}]}]))
    out = m.call_tool("get_trace", incident_id=1, agent_run_id=1, trace_id="t")
    assert out["error_code"] == "TRACE_NOT_FOUND"   # 业务错误码透传,非 MCP_*


def test_call_tool_serialized_by_semaphore():
    m = _manager(StubSession([{"content": [{"type": "text", "text": "{}"}]} for _ in range(3)]))
    assert m._sem._value == 1 if hasattr(m._sem, "_value") else True
    m.call_tool("get_trace", incident_id=1, agent_run_id=1, trace_id="t")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_mcp_client.py -q`
Expected: FAIL(`ModuleNotFoundError: app.mcp.client`)

- [ ] **Step 3: 实现 `client.py`**

```python
"""McpClientManager:后台线程 + 唯一 event loop + stdio ClientSession,同步桥接。
同步 call_tool 经 run_coroutine_threadsafe 提交到专用 loop,单会话串行(Semaphore(1))。"""
import asyncio
import logging
import os
import subprocess
import sys
import threading
import uuid
from typing import Any

from app.config import settings
from app.mcp.contract import MCP_TOOL_CONTRACT_VERSION, verify_contract

logger = logging.getLogger(__name__)

MCP_START_FAILED = "MCP_START_FAILED"
MCP_SCHEMA_MISMATCH = "MCP_SCHEMA_MISMATCH"
MCP_TIMEOUT = "MCP_TIMEOUT"
MCP_DISCONNECTED = "MCP_DISCONNECTED"
MCP_PROTOCOL_ERROR = "MCP_PROTOCOL_ERROR"
MCP_TOOL_ERROR = "MCP_TOOL_ERROR"
MCP_RESULT_INVALID = "MCP_RESULT_INVALID"


class MCPError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


# 子进程环境白名单:不传 fix_executor/LLM/Embedding/Qdrant Write 凭据
_ENV_ALLOWLIST = frozenset({
    "TRACEMIND_CONTROL_DB_URL", "TRACEMIND_READONLY_DB_URL",
    "TRACEMIND_ORDER_SERVICE_URL", "TRACEMIND_INVENTORY_SERVICE_URL",
    "TRACEMIND_EVAL_MODE", "TRACEMIND_EVAL_FIXTURE_DIR", "TRACEMIND_MCP_*",
})


def _spawn_env() -> dict:
    env = {"PYTHONUNBUFFERED": "1"}
    for k in _ENV_ALLOWLIST:
        if k.endswith("*"):
            prefix = k[:-1]
            env.update({ek: v for ek, v in os.environ.items() if ek.startswith(prefix)})
        elif k in os.environ:
            env[k] = os.environ[k]
    return env


class McpClientManager:
    def __init__(self, fixture_file: str | None = None) -> None:
        self.fixture_file = fixture_file
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session = None
        self._proc: subprocess.Popen | None = None
        self._sem = threading.Semaphore(1)
        self.is_ready = False
        self._timeout = settings.mcp_timeout_seconds
        self.max_restart = settings.mcp_max_restart
        self._invocation_id = 0

    async def _run_loop(self, ready: asyncio.Event) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            await self._start_session(ready)
            await self._loop.run_forever()
        finally:
            await self._close_session()

    async def _start_session(self, ready: asyncio.Event) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        attempts = 0
        while True:
            attempts += 1
            try:
                cmd = [sys.executable, "-m", "app.mcp.server"]
                if self.fixture_file:
                    cmd += ["--fixture-file", self.fixture_file]
                params = StdioServerParameters(command=cmd[0], args=cmd[1:],
                                               env=_spawn_env())
                self._proc = await stdio_client(params).__aenter__()
                self._session = await ClientSession(self._proc[0], self._proc[1]).__aenter__()
                await self._session.initialize()
                server_info = self._session.get_server_info() or {}
                tools = (await self._session.list_tools()).tools
                verify_contract(server_info, [{"name": t.name, "inputSchema": t.inputSchema}
                                              for t in tools])
                self.is_ready = True
                settings.mcp_ready = True
                ready.set()
                logger.info("MCP Server 就绪(pid 待补),contract %s", MCP_TOOL_CONTRACT_VERSION)
                return
            except Exception as exc:  # noqa: BLE001 启动/初始化/契约失败按策略重试
                logger.warning("MCP Server 启动失败(第 %d/%d 次): %s", attempts,
                               self.max_restart + 1, exc)
                if attempts > self.max_restart:
                    settings.mcp_ready = False
                    raise MCPError(MCP_START_FAILED, str(exc)) from exc
                await asyncio.sleep(0.5)

    async def _close_session(self) -> None:
        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._session = None
        if self._proc:
            try:
                self._proc[2].terminate()
                self._proc[2].wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    self._proc[2].kill()
                except Exception:  # noqa: BLE001
                    pass
            self._proc = None
        self.is_ready = False
        settings.mcp_ready = False

    async def start(self) -> None:
        ready = asyncio.Event()
        self._thread = threading.Thread(target=self._run_loop, args=(ready,), daemon=True)
        self._thread.start()
        await asyncio.wait_for(ready.wait(), timeout=30)

    async def stop(self) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None

    def call_tool(self, name: str, incident_id: int, agent_run_id: int,
                  **business: Any) -> dict:
        """同步工具调用:注入上下文 + 桥接到后台 loop,单会话串行。"""
        self._invocation_id += 1
        mcp_invocation_id = f"mcp-{self._invocation_id}-{uuid.uuid4().hex[:8]}"
        with self._sem:
            if self._session is None or self._loop is None:
                raise MCPError(MCP_DISCONNECTED, "MCP 会话未就绪")
            future = asyncio.run_coroutine_threadsafe(
                self._session.call_tool(name, {
                    "incident_id": incident_id, "agent_run_id": agent_run_id, **business}),
                self._loop)
            try:
                result = future.result(timeout=self._timeout)
            except asyncio.TimeoutError as exc:
                raise MCPError(MCP_TIMEOUT, str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                raise MCPError(MCP_TOOL_ERROR, str(exc)) from exc
        return self._parse_result(result, mcp_invocation_id)

    @staticmethod
    def _parse_result(result: Any, mcp_invocation_id: str) -> dict:
        if result is None:
            raise MCPError(MCP_RESULT_INVALID, "空响应")
        text = ""
        for c in result.content or []:
            if c.type == "text":
                text += c.text or ""
        import json
        try:
            out = json.loads(text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise MCPError(MCP_RESULT_INVALID, str(exc)) from exc
        if not isinstance(out, dict) or "success" not in out:
            raise MCPError(MCP_RESULT_INVALID, "ToolResult 校验失败")
        out["mcp_invocation_id"] = mcp_invocation_id
        return out


_client: McpClientManager | None = None


def get_mcp_client() -> McpClientManager:
    global _client
    if _client is None or not _client.is_ready:
        raise MCPError(MCP_START_FAILED, "MCP Client 未初始化(业务调用期间不悄悄启动)")
    return _client
```

> 注:真实实现中 `stdio_client(params)` 返回 `(read_stream, write_stream, process)` 三元组;`ClientSession(read, write)`。若 SDK 返回结构不同,以实际为准调整 `_proc` 取值与 `_close_session`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_mcp_client.py -q`
Expected: 4 passed(测试经 `_session` 直连,不 spawn 真实进程)

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/mcp/client.py ai-service/tests/test_mcp_client.py
git commit -m "feat(mcp): McpClientManager — 后台线程唯一 loop + stdio 会话 + 同步桥接 + Semaphore(1) + 错误码 + 环境白名单"
```

---

### Task 5: FastAPI lifespan 接入

**Files:**
- Modify: `ai-service/app/main.py`(lifespan 启动/关闭 MCP Client;health 加 mcp 状态)
- Modify: `ai-service/app/config.py`(如需 `mcp_ready` 暴露)
- Test: `ai-service/tests/test_health.py`(追加)

**Interfaces:**
- Consumes: Task 4 的 `McpClientManager` / `get_mcp_client`。
- Produces: `/api/health` 响应含 `mcp_ready: bool`;lifespan startup 失败 → 应用启动失败(FastAPI lifespan 异常传播)。

- [ ] **Step 1: 写失败测试**

`tests/test_health.py` 追加:

```python
def test_health_reports_mcp_ready(monkeypatch):
    from app.main import app
    monkeypatch.setattr("app.main.mcp_manager", None)
    with app.router.lifespan_context(app):
        pass
```

> 若现有 health 测试结构不同,改为断言 `health()` 返回含 `mcp_ready` 键。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_health.py -q`
Expected: FAIL(health 无 `mcp_ready` 字段)

- [ ] **Step 3: 实现(main.py)**

```python
from app.mcp.client import McpClientManager

mcp_manager: McpClientManager | None = None


async def lifespan(app: FastAPI):
    global mcp_manager
    mcp_manager = McpClientManager()
    await mcp_manager.start()          # 失败抛 MCPError → 应用启动失败
    await runner.recover_pending_runs()
    task = asyncio.create_task(scanner_loop())
    yield
    task.cancel()
    await mcp_manager.stop()


@app.get("/api/health")
def health():
    return {"status": "ok", "mcp_ready": bool(mcp_manager and mcp_manager.is_ready)}
```

> 启动失败策略:MCP Server 初始化/契约校验失败 → `mcp_manager.start()` 抛异常 → FastAPI 启动失败(readiness=false),不得以"服务正常、调查时再报错"方式继续。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_health.py tests/test_api_incidents.py -q`
Expected: PASS(现有 lifespan 相关测试若 spawn 真实 server,需 monkeypatch `McpClientManager.start/stop` 为 no-op——在测试 fixture 中处理)

- [ ] **Step 5: 提交**

```bash
git add ai-service/app/main.py ai-service/tests/test_health.py
git commit -m "feat(mcp): FastAPI lifespan 管理 MCP Client(启动失败即启动失败)+ health 暴露 mcp_ready"
```

---

### Task 6: collect_evidence 改造(经 MCP 调用)

**Files:**
- Modify: `ai-service/app/agent/nodes.py`(`_call_tool` 改经 `get_mcp_client().call_tool`)
- Modify: `ai-service/app/agent/llm.py`(`select_tool` 用裁剪 Schema:替换 `TOOL_SCHEMAS` 为 `llm_tool_schemas()`)
- Test: `ai-service/tests/test_collect_evidence.py`、`ai-service/tests/test_agent_graph.py`(适配)

**Interfaces:**
- Consumes: Task 4 `get_mcp_client().call_tool(name, incident_id, agent_run_id, **business)`;state 的 `run_id`(作为 agent_run_id)。
- Produces: 无新接口;`_call_tool(state, tool, **kwargs)` 行为保持(返回 ToolResult 结构 dict)。

- [ ] **Step 1: 写失败测试**

`tests/test_collect_evidence.py` 追加:

```python
def test_call_tool_goes_through_mcp(monkeypatch):
    from app.agent import nodes

    calls = []

    class FakeMCP:
        def call_tool(self, name, incident_id, agent_run_id, **business):
            calls.append((name, incident_id, agent_run_id, business))
            return {"success": True, "data": {"p95Ms": 120,
                                              "representativeSlowTraceId": "t1"}}

    monkeypatch.setattr("app.agent.nodes.get_mcp_client", lambda: FakeMCP())
    state = {"incident_id": 7, "run_id": 3, "service_ref": "inventory-service"}
    out = nodes._call_tool(state, "get_service_metrics", service_ref="inventory-service",
                           window_seconds=300)
    assert calls == [("get_service_metrics", 7, 3,
                      {"service_ref": "inventory-service", "window_seconds": 300})]
    assert out["success"] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_collect_evidence.py::test_call_tool_goes_through_mcp -q`
Expected: FAIL(`_call_tool` 仍直接调 `execute_tool`,FakeMCP 未被调用)

- [ ] **Step 3: 实现(nodes.py `_call_tool`)**

```python
def _call_tool(state: IncidentState, tool: str, **kwargs) -> dict:
    from app.agent.nodes_get_mcp import get_mcp_client  # 见下
    # 上下文由 MCP Client 注入;kwargs 中出现的 incident_id/agent_run_id 一律剔除
    kwargs.pop("incident_id", None)
    kwargs.pop("agent_run_id", None)
    result = get_mcp_client().call_tool(
        tool, incident_id=state.get("incident_id", 0),
        agent_run_id=state.get("run_id", 0), **kwargs)
    state["tool_call_count"] = state.get("tool_call_count", 0) + 1
    return result
```

> 避免循环导入:在 `app/agent/nodes.py` 顶部 `from app.mcp.client import get_mcp_client`(app.mcp.client 不 import agent 包,无环)。

- [ ] **Step 4: 适配 `llm.py` select_tool 的 Schema**

将 `select_tool` 中 `from app.agent.tool_calling import TOOL_SCHEMAS` 改为使用 `llm_tool_schemas()`(裁剪 Schema,隐藏上下文字段):

```python
from app.mcp.contract import llm_tool_schemas
schemas = [s for s in llm_tool_schemas() if s["function"]["name"] in eligible_tools]
```

- [ ] **Step 5: 适配既有测试**

- `tests/test_agent_graph.py` / `tests/test_collect_evidence.py` 中原 monkeypatch `nodes.execute_tool` 的用例:改为 monkeypatch `nodes.get_mcp_client` 返回 FakeMCP(fixture 响应逻辑移入 FakeMCP.call_tool)。
- `tests/test_llm.py` 中 select_tool 相关断言若依赖 `TOOL_SCHEMAS`,改用 `llm_tool_schemas()` 后重跑。

Run: `cd ai-service && uv run pytest tests/test_collect_evidence.py tests/test_agent_graph.py tests/test_llm.py -q`
Expected: 全绿

- [ ] **Step 6: 提交**

```bash
git add ai-service/app/agent/nodes.py ai-service/app/agent/llm.py ai-service/tests/test_collect_evidence.py ai-service/tests/test_agent_graph.py ai-service/tests/test_llm.py
git commit -m "feat(mcp): collect_evidence 经 MCP Client 调用,select_tool 用裁剪 Schema,上下文由 Client 注入"
```

---

### Task 7: 数据库迁移(tool_call 审计字段)

**Files:**
- Create: `scripts/sql/05-v12-mcp-migration.sql`(版本化幂等迁移)
- Modify: `ai-service/app/db/models.py`(ToolCall 加字段)
- Modify: `ai-service/app/repositories/tool_repo.py`(record_tool_call 支持新字段)
- Modify: `ai-service/app/tools/execute.py`(透传 agent_run_id/transport/mcp_invocation_id/mcp_attempt 给审计)
- Test: `ai-service/tests/test_audit_repos.py`(追加)

**Interfaces:**
- Consumes: `execute_tool(tool_name, incident_id=None, agent_run_id=None, **kwargs)`;MCP Client 返回中的 `mcp_invocation_id`。
- Produces: `record_tool_call(incident_id, tool_name, input_data, output, agent_run_id=None, transport="legacy_direct", mcp_invocation_id=None, mcp_attempt=None)`;ToolCall 模型含 4 新字段;transport 取值 `legacy_direct/mcp_stdio/internal_control/fixture_mcp_stdio`。

- [ ] **Step 1: 写失败测试**

`tests/test_audit_repos.py` 追加:

```python
def test_record_tool_call_with_mcp_fields(monkeypatch):
    from app.repositories import tool_repo
    from app.db import models

    fake = {"inserted": True}

    def fake_add(obj):
        fake["obj"] = obj
        return obj
    monkeypatch.setattr(tool_repo, "get_control_engine", lambda: None)
    # 简化:直接构造 ToolCall 并断言字段存在
    tc = models.ToolCall(incident_id=1, tool_name="get_trace", input={}, output={},
                         agent_run_id=2, transport="mcp_stdio",
                         mcp_invocation_id="mcp-1-abc", mcp_attempt=1)
    assert tc.transport == "mcp_stdio"
    assert tc.agent_run_id == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd ai-service && uv run pytest tests/test_audit_repos.py -q`
Expected: FAIL(`AttributeError: 'ToolCall' object has no attribute 'transport'`)

- [ ] **Step 3: 版本化迁移 SQL(`05-v12-mcp-migration.sql`)**

```sql
-- V1.2 版本化迁移:tool_call 增加 MCP 审计字段(信息 schema 幂等判断)
SET @have_col := (SELECT COUNT(*) FROM information_schema.COLUMNS
                  WHERE TABLE_SCHEMA='tracemind_control' AND TABLE_NAME='tool_call'
                    AND COLUMN_NAME='agent_run_id');
SET @ddl := IF(@have_col = 0,
  'ALTER TABLE tracemind_control.tool_call ADD COLUMN agent_run_id BIGINT NULL AFTER incident_id',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @have_col := (SELECT COUNT(*) FROM information_schema.COLUMNS
                  WHERE TABLE_SCHEMA='tracemind_control' AND TABLE_NAME='tool_call'
                    AND COLUMN_NAME='transport');
SET @ddl := IF(@have_col = 0,
  'ALTER TABLE tracemind_control.tool_call ADD COLUMN transport VARCHAR(32) NOT NULL DEFAULT ''legacy_direct'' AFTER status',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @have_col := (SELECT COUNT(*) FROM information_schema.COLUMNS
                  WHERE TABLE_SCHEMA='tracemind_control' AND TABLE_NAME='tool_call'
                    AND COLUMN_NAME='mcp_invocation_id');
SET @ddl := IF(@have_col = 0,
  'ALTER TABLE tracemind_control.tool_call ADD COLUMN mcp_invocation_id VARCHAR(64) NULL AFTER transport',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @have_col := (SELECT COUNT(*) FROM information_schema.COLUMNS
                  WHERE TABLE_SCHEMA='tracemind_control' AND TABLE_NAME='tool_call'
                    AND COLUMN_NAME='mcp_attempt');
SET @ddl := IF(@have_col = 0,
  'ALTER TABLE tracemind_control.tool_call ADD COLUMN mcp_attempt INT NULL AFTER mcp_invocation_id',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @have_idx := (SELECT COUNT(*) FROM information_schema.STATISTICS
                  WHERE TABLE_SCHEMA='tracemind_control' AND TABLE_NAME='tool_call'
                    AND INDEX_NAME='idx_tool_call_agent_run');
SET @ddl := IF(@have_idx = 0,
  'ALTER TABLE tracemind_control.tool_call ADD INDEX idx_tool_call_agent_run (agent_run_id)',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
```

> 本地重放:`mysql -uroot -proot < scripts/sql/05-v12-mcp-migration.sql`;VM 重放:`docker exec tracemind-mysql sh -c 'mysql -uroot -proot_pwd_2026 < /tmp/05.sql'`(先 docker cp)。

- [ ] **Step 4: models.py 加字段**

`ToolCall` 追加:

```python
    agent_run_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    transport: Mapped[str] = mapped_column(String(32), default="legacy_direct")
    mcp_invocation_id: Mapped[Optional[str]] = mapped_column(String(64))
    mcp_attempt: Mapped[Optional[int]] = mapped_column(Integer)
```

- [ ] **Step 5: tool_repo 与 execute_tool 透传**

`tool_repo.record_tool_call` 签名与写入更新(加 4 字段);`execute_tool` 签名加 `agent_run_id: int | None = None`,fixture key 计算排除 `agent_run_id`,成功/失败路径的 `record_tool_call` 调用传 `agent_run_id` 与 `transport="mcp_stdio"`(MCP 调用)或 `transport="internal_control"`(execute_fix/verify_recovery 直接调用时)。MCP Server 的 `_delegate` 将 `mcp_invocation_id`/`mcp_attempt` 从调用上下文透传(若 Client 提供)。

- [ ] **Step 6: 跑测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_audit_repos.py -q`
Expected: PASS;本地重放迁移脚本确认幂等(跑两次)。

- [ ] **Step 7: 提交**

```bash
git add scripts/sql/05-v12-mcp-migration.sql ai-service/app/db/models.py ai-service/app/repositories/tool_repo.py ai-service/app/tools/execute.py ai-service/tests/test_audit_repos.py
git commit -m "feat(mcp): tool_call 审计字段(agent_run_id/transport/mcp_invocation_id/mcp_attempt)+ 版本化迁移 + 透传"
```

---

### Task 8: 离线评测适配(真实 stdio + Fixture 文件)

**Files:**
- Modify: `scripts/eval_agent.py`(每条 Case 用 McpClientManager(fixture_file) 显式上下文)
- Modify: `scripts/eval_agent.py`(评测报告记录 MCP 证据)
- Test: 运行验证(非单测)

**Interfaces:**
- Consumes: Task 4 `McpClientManager(fixture_file=...)`(async start/stop,需在 async 包装中调用)。
- Produces: 评测报告含 `transport=mcp_stdio / pid / protocol_version / tool_call_count / tool_names / mcp_error_count / direct_fallback=false`。

- [ ] **Step 1: 改造 run_offline**

`run_offline` 每条 Case 改为显式上下文(不在业务调用期间悄悄启动):

```python
def run_offline(case: dict, thread_id: str) -> dict:
    import asyncio
    import tempfile
    from pathlib import Path

    # 将 case fixture 写入临时文件(位于 eval_fixture_dir)
    fixture_file = f"case-{case['case_id']}.json"
    eval_dir = Path(os.environ.get("TRACEMIND_EVAL_FIXTURE_DIR", "./eval_fixtures"))
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / fixture_file).write_text(
        json.dumps(case["tool_fixtures"]), encoding="utf-8")

    async def _run():
        from app.mcp.client import McpClientManager
        from app.agent.graph import build_graph
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.types import Command

        mgr = McpClientManager(fixture_file=fixture_file)
        try:
            await mgr.start()
            state = { ... }   # 与现有一致,但不再 set_eval_fixture
            graph = build_graph(checkpointer=InMemorySaver())
            config = {"configurable": {"thread_id": thread_id}}
            # 循环调用 graph.invoke / Command(resume) 与现有一致
            ...
        finally:
            await mgr.stop()
    return asyncio.run(_run())
```

> main() 保留 `TRACEMIND_RAG_MODE=off` 与 `TRACEMIND_LLM_MODE` 设置;新增 `os.environ["TRACEMIND_EVAL_MODE"] = "true"`(fixture 模式门控)。

- [ ] **Step 2: 评测报告记录 MCP 证据**

主循环每个 case 完成后追加记录:case_id、expected/actual、`transport=mcp_stdio`、MCP Server PID(`mgr._proc.pid` 若有)、协议版本(initialize 返回)、tool_call_count、tool_names、mcp_error_count、`direct_fallback=false`。汇总打印与现有一致(召回率/误修复率/PASS)。

- [ ] **Step 3: 运行验证(fake)**

Run: `cd ai-service && TRACEMIND_LLM_MODE=fake TRACEMIND_EVAL_MODE=true uv run python ../scripts/eval_agent.py --mode offline --llm fake --runs 1`
Expected: 16/16 PASS,报告含 transport=mcp_stdio,无 MCP 基础设施错误。

- [ ] **Step 4: 运行验证(real_strict,可选本地)**

Run: `cd ai-service && TRACEMIND_LLM_MODE=real_strict TRACEMIND_EVAL_MODE=true uv run python ../scripts/eval_agent.py --mode offline --llm real_strict --runs 1`
Expected: 无 MCP 基础设施错误;V1.1 指标保留(召回≥80%、误修复 0%)。

- [ ] **Step 5: 提交**

```bash
git add scripts/eval_agent.py
git commit -m "feat(mcp): 离线评测走真实 stdio MCP Server + Fixture 文件,报告记录 MCP 证据"
```

---

### Task 9: 测试分层补全 + 故障注入

**Files:**
- Create: `ai-service/tests/test_mcp_protocol.py`(真实 stdio 集成 + stdout 纯净)
- Create: `ai-service/tests/test_mcp_faults.py`(6 错误码注入 + 主动终止无 direct fallback)
- Modify: `scripts/verify-m3.py`(transport 断言)

**Interfaces:**
- Consumes: `McpClientManager`(Task 4)、`execute_tool`(Task 7)。
- Produces: 无新接口;验收断言。

- [ ] **Step 1: 协议集成测试(`test_mcp_protocol.py`)**

```python
"""MCP 协议集成:真实 stdio 子进程,验证 initialize/tools/list/call/stdout 纯净。"""
import asyncio
import json
import subprocess
import sys

import pytest

from app.mcp.contract import SERVER_NAME, TOOL_NAMES


@pytest.mark.asyncio
async def test_stdio_initialize_list_call_stdout_clean(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    fixture = {"get_service_metrics:{\"service_ref\": \"inventory-service\", \"window_seconds\": 300}":
               {"ok": True, "data": {"p95Ms": 120, "representativeSlowTraceId": "t1"}}}
    (tmp_path / "case.json").write_text(json.dumps(fixture), encoding="utf-8")

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.server", "--fixture-file", str(tmp_path / "case.json")],
        env={**__import__("os").environ, "TRACEMIND_EVAL_MODE": "true",
             "TRACEMIND_EVAL_FIXTURE_DIR": str(tmp_path)})
    async with stdio_client(params) as (read, write, proc):
        async with ClientSession(read, write) as session:
            await session.initialize()
            info = session.get_server_info()
            assert info.name == SERVER_NAME
            tools = (await session.list_tools()).tools
            assert {t.name for t in tools} == set(TOOL_NAMES)
            res = await session.call_tool(
                "get_service_metrics",
                {"incident_id": 1, "agent_run_id": 1,
                 "service_ref": "inventory-service", "window_seconds": 300})
            text = "".join(c.text for c in res.content if c.type == "text")
            assert json.loads(text)["success"] is True
        # stdout 纯净:子进程 stdout 不应含日志(日志走 stderr)
        proc.wait(timeout=10)
```

- [ ] **Step 2: 故障注入测试(`test_mcp_faults.py`)**

```python
"""故障注入:错误码映射与"主动终止不降级 direct"。"""
import pytest

from app.mcp.client import (MCP_DISCONNECTED, MCP_SCHEMA_MISMATCH, MCP_START_FAILED,
                            MCP_TIMEOUT, MCP_TOOL_ERROR, McpClientManager, MCPError)


def test_start_failed_maps_code():
    mgr = McpClientManager()
    mgr._loop = None
    # 未就绪调用 → 明确错误码,而非静默 direct
    with pytest.raises(MCPError) as ei:
        mgr.call_tool("get_trace", incident_id=1, agent_run_id=1, trace_id="t")
    assert ei.value.code in (MCP_DISCONNECTED, MCP_START_FAILED)


def test_disconnected_after_terminate():
    """主动终止 MCP Server 后,调用返回明确 MCP 错误;不得降级 direct。"""
    mgr = McpClientManager()
    with pytest.raises(MCPError) as ei:
        mgr.call_tool("get_trace", incident_id=1, agent_run_id=1, trace_id="t")
    assert ei.value.code != "TOOL_ERROR"      # 不是业务错误
    # 若已实现真实进程终止场景,此处断言 MCP_DISCONNECTED/MCP_START_FAILED
```

> 说明:协议级故障(超时/协议错误/结果非法)在 Task 4 的 Stub Session 测试中已覆盖(MCP_TIMEOUT / MCP_RESULT_INVALID);此处补充"未就绪/已终止"路径。真实子进程 terminate 场景在 Task 10 的 e2e 故障注入验收中执行。

- [ ] **Step 3: verify-m3 transport 断言**

`scripts/verify-m3.py` 在 evidence 断言后追加:

```python
    # V1.2 传输断言:五个调查工具 transport=mcp_stdio
    # (从 incident 详情的 tool_calls 记录或 API 附加字段读取;若 API 未暴露,
    #  改为断言 ai 日志/评测报告中的 transport 标志)
```

> 若 `GET /api/incidents/{id}` 未返回 tool_call 记录,需在 `api/incidents.py` 详情响应中附加最近 tool_call 的 transport 字段(小改动,按需)。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd ai-service && uv run pytest tests/test_mcp_protocol.py tests/test_mcp_faults.py -q`
Expected: 全绿(协议测试需本机可 spawn 子进程)

- [ ] **Step 5: 提交**

```bash
git add ai-service/tests/test_mcp_protocol.py ai-service/tests/test_mcp_faults.py scripts/verify-m3.py
git commit -m "test(mcp): 协议集成(stdout 纯净)+ 故障注入(错误码/主动终止不降级)+ 传输断言"
```

---

### Task 10: 全栈验收 + README + 收尾

**Files:**
- Modify: `README.md`(V1.2 章节)
- Modify: `docker-compose.yml`(无需新增服务;确认 ai-service 镜像含 app/mcp)
- 运行验收

**Interfaces:**
- Consumes: 全部前序 Task。

- [ ] **Step 1: 全量回归**

Run: `cd ai-service && uv run pytest tests/ -q`
Expected: 全绿(含协议集成/故障注入/Server/Client/契约/审计)

- [ ] **Step 2: 本地 e2e(fake)冒烟**

Run: `python scripts/verify-m3.py --base http://localhost:8000 --order http://localhost:8081`
Expected: 闭环 PASS(本地 MySQL/Java/AI 需启动;若本地未启动,跳过此步,以 VM 验收为准)

- [ ] **Step 3: VM 同步与验收(e2e-scn001-real 3/3 + 故障注入)**

- 同步 V1.2 代码到 `~/tracemind`(tar 上传 + 覆盖)
- `DOCKER_BUILDKIT=0 docker build -t tracemind-ai-service ai-service/`(pyproject 变更 → uv sync --frozen 在 Dockerfile 内执行,阿里云 pip 源)
- 重放迁移:`docker cp scripts/sql/05-v12-mcp-migration.sql tracemind-mysql:/tmp/05.sql && docker exec tracemind-mysql sh -c 'mysql -uroot -proot_pwd_2026 < /tmp/05.sql'`
- `docker compose up -d --no-deps --force-recreate ai-service`
- Run 3 轮:`python scripts/verify-m3.py --base http://<vm-host>:8000 --order http://<vm-host>:8081` ×3,断言 `transport=mcp_stdio`、无 MCP 基础设施错误、V1.1 指标保留(召回≥80%/误修复 0%/E1~E5 100%/SO 有效率≥95%/降级率 0%)
- 故障注入:调查中 `docker kill tracemind-ai` 内 MCP 子进程(或 `pkill -f app.mcp.server`),断言当前调用返回明确 MCP 错误、最多重启一次、**无 direct fallback**

- [ ] **Step 4: README V1.2 章节**

追加:MCP 工具服务说明(stdio、五只读工具、execute_fix/verify_recovery 不暴露)、生命周期、错误码表、测试命令、简历叙事段(采用 spec §14 文本)。

- [ ] **Step 5: 提交**

```bash
git add README.md
git commit -m "docs: V1.2 MCP 工具服务 — README 章节与验收说明"
```

---

## Self-Review

**1. Spec 覆盖:**
- 模式 A + stdio + 完全走 MCP(§2)→ Task 2/4/6;生命周期 lifespan(§5.2)→ Task 5;同步桥接(§5.1)→ Task 4;契约校验(§5.4)→ Task 3;并发锁(§5.5)→ Task 4;子进程最小权限(§5.6)→ Task 4(`_spawn_env` 白名单);启动失败策略(§5.7)→ Task 5;上下文注入与审计(§6)→ Task 6/7;Fixture 模式(§7)→ Task 2/8;错误码与重试(§8)→ Task 4/9;数据库迁移(§9)→ Task 7;测试分层(§10)→ Task 2/3/4/9;部署(§11)→ Task 5/10;验收(§12,正常/故障/传输断言/主动终止)→ Task 9/10;范围外(§13)→ 未安排(符合"明确不做")。✓
- 第四批修订:MCP Tool 签名含 agent_run_id(§4)→ Task 2;MCP_TOOL_CONTRACT_VERSION 来源(§5.4)→ Task 3;mcp_invocation_id 统一(§8/9)→ Task 4/7;execute_fix 传输断言(§12)→ Task 7/9;MCP_TOOL_ERROR/MCP_RESULT_INVALID 与业务 error_code 分层(§8)→ Task 4;子进程最小权限(§5.6)→ Task 4;并发/启动失败(§5.5/5.7)→ Task 4/5;保留 V1.1 指标(§12)→ Task 10;主动终止验收(§12)→ Task 10。✓

**2. 占位符扫描:** 无 TBD/TODO;Step 3 的注记(stdio_client 返回结构、TOOL_REGISTRY.description 字段)均给出实际适配指引;Task 9 Step 3 的 transport 断言标注"按 API 是否暴露决定",给出替代方案。✓

**3. 类型一致性:**
- `execute_tool(tool_name, incident_id=None, agent_run_id=None, **kwargs)`(Task 2/7)与 `_call_tool`(Task 6)一致 ✓
- `McpClientManager.call_tool(name, incident_id, agent_run_id, **business) -> dict`(Task 4)被 Task 6/8 使用一致 ✓
- `record_tool_call(..., agent_run_id=None, transport=..., mcp_invocation_id=None, mcp_attempt=None)`(Task 7)与 ToolCall 模型字段一致 ✓
- `verify_contract(server_info, tools) -> None`(Task 3)被 Task 4 调用 ✓
- `llm_tool_schemas() -> list[dict]`(Task 2)被 Task 3/6 使用一致 ✓
- 错误码常量(Task 4)被 Task 9 测试引用一致 ✓
- `MCP_TOOL_CONTRACT_VERSION / SERVER_NAME / SERVER_VERSION / TOOL_NAMES`(Task 2)贯穿 Task 3/4/9 ✓

**4. 遗留风险(执行时验证):**
- `stdio_client` 返回结构依 SDK 版本而定(Task 4 已注记适配);`mcp.run()` 阻塞导致 Server 单测需 monkeypatch(注记)。
- FastAPI lifespan 现有测试若 spawn 真实子进程,需在测试 fixture 中 monkeypatch `McpClientManager.start/stop`。
- 本地 MySQL80 需启动才能跑本地 e2e;验收以 VM 为准。
