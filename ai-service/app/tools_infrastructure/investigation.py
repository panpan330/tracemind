"""调查数据端口实现:直通现有 services(过渡期;handler 只依赖 ports 接口)。"""
from app.repositories import incident_repo
from app.services import (index_info_service, metrics_service, query_plan_service,
                          slow_query_service, trace_service)
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
    def list_expensive_digests(self, incident_id=None, window_seconds=None):
        return slow_query_service.list_expensive_digests(incident_id)


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
