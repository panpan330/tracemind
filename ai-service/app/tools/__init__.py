# 工具注册:五个 LLM 可调用调查工具 + 两个确定性节点工具
from app.repositories import incident_repo
from app.services import (index_info_service, metrics_service, query_plan_service,
                          recovery_service, slow_query_service, fix_service,
                          trace_service)
from app.tools import lock_queries
from app.tools.registry import TOOL_REGISTRY, ToolSpec
from app.tools.schemas import (ExecuteFixIn, GetIndexInfoIn, GetLockWaitersIn,
                               GetQueryPlanIn, GetServiceMetricsIn,
                               GetTraceIn, GetTransactionDetailsIn, ListDigestsIn,
                               VerifyRecoveryIn)


def _get_metrics(service_ref: str, window_start: str | None = None,
                 window_end: str | None = None, **kw) -> dict:
    """V1.4 指标门面:显式窗口优先;兼容 window_seconds 回退。"""
    if window_start and window_end:
        return metrics_service.get_metrics(service_ref, window_start, window_end)
    # 回退:以当前时间向前推 window_seconds
    import datetime
    end = datetime.datetime.now(datetime.timezone.utc).isoformat()
    start = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(seconds=kw.get("window_seconds") or 300)).isoformat()
    return metrics_service.get_metrics(service_ref, start, end)


def _get_trace(trace_ref: str | None = None, trace_id: str | None = None,
               incident_id: int | None = None, **kw) -> dict:
    """V1.4 trace 门面:从 Incident 解析 service/operation 上下文。"""
    incident = incident_repo.get_incident(incident_id) if incident_id else None
    inc_dict = {}
    if incident is not None:
        inc_dict = {"id": incident.id,
                    "affected_service_ref": incident.affected_service_ref,
                    "affected_operation_ref": incident.affected_operation_ref,
                    "observed_at": str(incident.created_at)}
    return trace_service.get_trace(trace_ref, trace_id, inc_dict)


def _get_lock_waiters(scope_ref: str) -> dict:
    """scope_ref 枚举白名单;schema/table/min_wait_ms 由程序固定(LLM 无自由参数)。
    返回纯净 data(execute_tool 负责 ToolResult 包装);失败抛 ValueError。"""
    r = lock_queries.get_lock_waiters("tracemind_business", "inventory", 3000)
    if not r.get("ok"):
        raise ValueError(r.get("error_message") or "lock_waiters_query_failed")
    return r["data"]


def _get_transaction_details(transaction_ref: str) -> dict:
    r = lock_queries.get_transaction_details(transaction_ref)
    if not r.get("ok"):
        raise ValueError(r.get("error_message") or "trx_query_failed")
    return r["data"]


TOOL_REGISTRY.update({
    "get_service_metrics": ToolSpec("get_service_metrics", GetServiceMetricsIn,
                                    _get_metrics),
    "get_trace": ToolSpec("get_trace", GetTraceIn, _get_trace),
    "list_expensive_query_digests": ToolSpec(
        "list_expensive_query_digests", ListDigestsIn, slow_query_service.list_expensive_digests),
    "get_query_plan": ToolSpec("get_query_plan", GetQueryPlanIn, query_plan_service.explain),
    "get_index_info": ToolSpec("get_index_info", GetIndexInfoIn, index_info_service.get_index_info),
    "get_lock_waiters": ToolSpec("get_lock_waiters", GetLockWaitersIn, _get_lock_waiters),
    "get_transaction_details": ToolSpec("get_transaction_details", GetTransactionDetailsIn,
                                        _get_transaction_details),
    "execute_fix": ToolSpec("execute_fix", ExecuteFixIn, fix_service.execute_fix),
    "verify_recovery": ToolSpec("verify_recovery", VerifyRecoveryIn,
                                recovery_service.verify_recovery),
})
