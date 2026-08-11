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
    data: dict[str, Any] | list | None = None
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


class GetLockWaitersIn(BaseModel):
    scope_ref: str = Field(pattern="^(INVENTORY_RESERVATION)$")


class GetTransactionDetailsIn(BaseModel):
    # 受控引用:占位符 OBSERVED_BLOCKER(planner 规划)或程序解析后的 blk_<processlist_id>
    transaction_ref: str = Field(pattern=r"^(OBSERVED_BLOCKER|blk_\d+)$")


class ExecuteFixIn(BaseModel):
    incident_id: int = Field(gt=0)
    fix_proposal_id: int = Field(gt=0)
    approval_id: int = Field(gt=0)


class VerifyRecoveryIn(BaseModel):
    incident_id: int = Field(gt=0)
    fix_execution_id: int = Field(gt=0)
