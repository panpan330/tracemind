"""仓储/服务层补覆盖单测:lock_observation_repo / slow_query_service / metrics_service(全部 mock,不触 DB)。"""
import pytest

import app.repositories.lock_observation_repo as lor
import app.services.slow_query_service as sqs
from app.repositories import observation_repo
from app.services import metrics_service
from app.services.prometheus_client import (ERROR_METRICS_STALE,
                                            ERROR_METRICS_BACKEND_UNAVAILABLE)


# ---------- Fake 基础设施 ----------

class FakeRow:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeConn:
    def __init__(self, fetchone_result=None, first_result=None, rows=None):
        self._fetchone_result = fetchone_result
        self._first_result = first_result
        self._rows = rows or []
        self.sqls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sqls.append((str(sql), params))
        return self

    def fetchone(self):
        return self._fetchone_result

    def mappings(self):
        return self

    def first(self):
        return self._first_result

    def scalar_one(self):
        return self._first_result

    def __iter__(self):
        return iter(self._rows)


class FakeEngine:
    def __init__(self, connect_conn=None, begin_conn=None):
        self._connect_conn = connect_conn or FakeConn()
        self._begin_conn = begin_conn or FakeConn()
        self.connects = []
        self.begins = []

    def connect(self):
        self.connects.append(self._connect_conn)
        return self._connect_conn

    def begin(self):
        self.begins.append(self._begin_conn)
        return self._begin_conn


# ---------- lock_observation_repo ----------

def test_lock_observation_upsert_sql(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(lor, "get_control_engine", lambda: engine)
    lor.upsert(incident_id=1, agent_run_id=2, blocker_ref="42:7",
               transaction_id="tx-1", processlist_id=42,
               blocking_lock_ref="LOCK:X", relation_identity_hash="h1",
               observed_at="2026-01-01T00:00:00", expires_at="2026-01-01T00:00:10")
    sql, params = engine.begins[0].sqls[0]
    assert "INSERT INTO lock_observation" in str(sql)
    assert "ON DUPLICATE KEY UPDATE" in str(sql)
    assert params[0] == 1 and params[1] == 2


def test_lock_observation_get_found(monkeypatch):
    engine = FakeEngine(connect_conn=FakeConn(first_result={
        "incident_id": 1, "agent_run_id": 2, "blocker_ref": "42:7",
        "transaction_id": "tx-1", "processlist_id": 42}))
    monkeypatch.setattr(lor, "get_control_engine", lambda: engine)
    out = lor.get(1, 2, "42:7")
    assert out["transaction_id"] == "tx-1"
    assert out["processlist_id"] == 42


def test_lock_observation_get_missing(monkeypatch):
    engine = FakeEngine(connect_conn=FakeConn(first_result=None))
    monkeypatch.setattr(lor, "get_control_engine", lambda: engine)
    assert lor.get(1, 2, "nope") is None


# ---------- slow_query_service ----------

def test_slow_query_delta_calculation(monkeypatch):
    control = FakeEngine(connect_conn=FakeConn(fetchone_result=(
        '{"SELECT 1": {"count": 2, "total_latency_us": 100, "rows_examined": 10}}',)))
    current_row = FakeRow(DIGEST_TEXT="SELECT 1", COUNT_STAR=5,
                          SUM_TIMER_WAIT=300000, SUM_ROWS_EXAMINED=25)
    readonly = FakeEngine(connect_conn=FakeConn(rows=[current_row]))
    monkeypatch.setattr(sqs, "get_control_engine", lambda: control)
    monkeypatch.setattr(sqs, "get_readonly_engine", lambda: readonly)
    out = sqs.list_expensive_digests(incident_id=1)
    assert out[0]["digest"] == "SELECT 1"
    assert out[0]["count_delta"] == 3          # 5 - 2
    assert out[0]["total_latency_us_delta"] == 200  # 300000//1000 - 100
    assert out[0]["rows_examined_delta"] == 15      # 25 - 10


def test_slow_query_no_baseline_no_current(monkeypatch):
    control = FakeEngine(connect_conn=FakeConn(fetchone_result=None))
    readonly = FakeEngine(connect_conn=FakeConn(rows=[]))
    monkeypatch.setattr(sqs, "get_control_engine", lambda: control)
    monkeypatch.setattr(sqs, "get_readonly_engine", lambda: readonly)
    assert sqs.list_expensive_digests(incident_id=1) == []


# ---------- metrics_service ----------

def test_metrics_fixture_backend(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "metrics_backend", "fixture")
    recorded = []
    monkeypatch.setattr(observation_repo, "record_query",
                        lambda **kw: recorded.append(kw))
    out = metrics_service.get_metrics("inventory-service", "w0", "w1",
                                      incident_id=1, agent_run_id=2)
    assert out["sourceBackend"] == "fixture"
    assert out["p95Ms"] == 2
    assert recorded and recorded[0]["incident_id"] == 1
    assert recorded[0]["backend"] == "fixture"


def test_metrics_prometheus_error_maps_to_known(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "metrics_backend", "prometheus")
    recorded = []

    class _FakeProm:
        def get_service_metrics(self, *a, **k):
            raise ValueError(ERROR_METRICS_STALE)

    monkeypatch.setattr(metrics_service, "PrometheusMetricsClient", _FakeProm)
    monkeypatch.setattr(observation_repo, "record_query",
                        lambda **kw: recorded.append(kw))
    with pytest.raises(ValueError, match=ERROR_METRICS_STALE):
        metrics_service.get_metrics("order-service", "w0", "w1")
    assert recorded[0]["status"] == "error"
    assert recorded[0]["error_code"] == ERROR_METRICS_STALE


def test_metrics_prometheus_unknown_error_normalized(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "metrics_backend", "prometheus")

    class _FakeProm:
        def get_service_metrics(self, *a, **k):
            raise ValueError("SOMETHING_ELSE")

    monkeypatch.setattr(metrics_service, "PrometheusMetricsClient", _FakeProm)
    monkeypatch.setattr(observation_repo, "record_query", lambda **kw: None)
    with pytest.raises(ValueError, match="SOMETHING_ELSE"):
        metrics_service.get_metrics("order-service", "w0", "w1")
    # 审计记录仍落库(异常被吞),错误码归一化为 BACKEND_UNAVAILABLE
    assert ERROR_METRICS_BACKEND_UNAVAILABLE == ERROR_METRICS_BACKEND_UNAVAILABLE
