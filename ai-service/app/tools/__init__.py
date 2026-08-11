# 工具注册:五个 LLM 可调用调查工具 + 两个确定性节点工具
from app.services import (index_info_service, metrics_service, query_plan_service,
                          recovery_service, slow_query_service, fix_service,
                          trace_service)
from app.tools import lock_queries
from app.tools.registry import TOOL_REGISTRY, ToolSpec
from app.tools.schemas import (ExecuteFixIn, GetIndexInfoIn, GetLockWaitersIn,
                               GetQueryPlanIn, GetServiceMetricsIn,
                               GetTraceIn, GetTransactionDetailsIn, ListDigestsIn,
                               VerifyRecoveryIn)


def _get_lock_waiters(scope_ref: str) -> dict:
    """scope_ref 枚举白名单;schema/table/min_wait_ms 由程序固定(LLM 无自由参数)。"""
    return lock_queries.get_lock_waiters("tracemind_business", "inventory", 3000)


def _get_transaction_details(transaction_ref: str) -> dict:
    return lock_queries.get_transaction_details(transaction_ref)


TOOL_REGISTRY.update({
    "get_service_metrics": ToolSpec("get_service_metrics", GetServiceMetricsIn,
                                    metrics_service.get_metrics),
    "get_trace": ToolSpec("get_trace", GetTraceIn, trace_service.get_trace),
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
