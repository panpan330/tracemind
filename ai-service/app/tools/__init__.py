# 工具注册:五个 LLM 可调用调查工具 + 两个确定性节点工具
from app.services import (index_info_service, metrics_service, query_plan_service,
                          recovery_service, slow_query_service, fix_service,
                          trace_service)
from app.tools.registry import TOOL_REGISTRY, ToolSpec
from app.tools.schemas import (ExecuteFixIn, GetIndexInfoIn, GetQueryPlanIn,
                               GetServiceMetricsIn, GetTraceIn, ListDigestsIn,
                               VerifyRecoveryIn)

TOOL_REGISTRY.update({
    "get_service_metrics": ToolSpec("get_service_metrics", GetServiceMetricsIn,
                                    metrics_service.get_metrics),
    "get_trace": ToolSpec("get_trace", GetTraceIn, trace_service.get_trace),
    "list_expensive_query_digests": ToolSpec(
        "list_expensive_query_digests", ListDigestsIn, slow_query_service.list_expensive_digests),
    "get_query_plan": ToolSpec("get_query_plan", GetQueryPlanIn, query_plan_service.explain),
    "get_index_info": ToolSpec("get_index_info", GetIndexInfoIn, index_info_service.get_index_info),
    "execute_fix": ToolSpec("execute_fix", ExecuteFixIn, fix_service.execute_fix),
    "verify_recovery": ToolSpec("verify_recovery", VerifyRecoveryIn,
                                recovery_service.verify_recovery),
})
