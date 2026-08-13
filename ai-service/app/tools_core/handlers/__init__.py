"""7 个只读调查 handler:参数提取 → ports 端口调用 → 纯净 data(不含业务实现)。

handler 禁止导入 services/agent/llm/fastapi/fastmcp(由导入边界测试保证)。
"""
from app.tools_core.ports import (DigestPort, IndexPort, LockPort, MetricsPort,
                                  PlanPort, TracePort)


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
