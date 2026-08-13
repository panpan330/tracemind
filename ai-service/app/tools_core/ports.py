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
                               result: Optional[dict] = None,
                               error_code: Optional[str] = None,
                               retryable: Optional[bool] = None,
                               latency_ms: int = 0) -> None:
        """写 completed/failed 终态审计。"""

    @abstractmethod
    def write_observation_query(self, ctx, tool_name: str, params: dict,
                                result: dict, latency_ms: int) -> None:
        """写入观测查询审计。"""


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
    def list_expensive_digests(self, window_seconds: Optional[int] = None) -> dict: ...


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
