# ai-service/tests/test_tools_core_handlers.py
import pytest
from app.tools_core.handlers import build_handlers
from app.tools_core.ports import LockPort, MetricsPort, TracePort, IndexPort
from app.tools_core.errors import ToolBusinessError


class FakeLock(LockPort):
    def get_lock_waiters(self, scope_ref: str) -> dict:
        if scope_ref != "inventory:42":
            raise ToolBusinessError("SCOPE_INVALID", "scope 必须来自前序证据", retryable=False)
        return {"blockers": []}

    def get_transaction_details(self, transaction_ref: str) -> dict:
        return {"tx": 1}


class FakeMetrics(MetricsPort):
    def get_metrics(self, service_ref, window_start, window_end, incident_id):
        return {"samples": [1, 2]}


class FakeIndex(IndexPort):
    def get_index_info(self, table_ref):
        return {"indexes": []}


def test_lock_handler_returns_data():
    handlers = build_handlers({"lock": FakeLock()})
    r = handlers["get_lock_waiters"](scope_ref="inventory:42")
    assert r == {"blockers": []}


def test_lock_handler_rejects_bad_scope():
    handlers = build_handlers({"lock": FakeLock()})
    with pytest.raises(ToolBusinessError):
        handlers["get_lock_waiters"](scope_ref="bad")


def test_handlers_keys():
    handlers = build_handlers({"lock": FakeLock(), "metrics": FakeMetrics(),
                               "index": FakeIndex()})
    assert set(handlers) == {"get_service_metrics", "get_trace", "list_expensive_query_digests",
                             "get_query_plan", "get_index_info", "get_lock_waiters",
                             "get_transaction_details"}


def test_metrics_handler_passthrough():
    handlers = build_handlers({"metrics": FakeMetrics()})
    r = handlers["get_service_metrics"](service_ref="inventory-service", window_start="a",
                                        window_end="b")
    assert r == {"samples": [1, 2]}
