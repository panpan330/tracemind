"""V1.13 eval_run repository 单测(FakeEngine,不连真实库)。"""
import pytest

from app.repositories import eval_run_repo


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    @property
    def lastrowid(self):
        return 42


class FakeConn:
    def __init__(self, rows_by_sql=None):
        self.rows_by_sql = rows_by_sql or {}
        self.last_sql = None
        self.last_params = None
        self.lastrowid = 42

    def execute(self, stmt, params=None):
        self.last_sql = str(stmt)
        self.last_params = params
        key = "SELECT" if "SELECT" in str(stmt).upper() else str(stmt)
        return FakeResult(self.rows_by_sql.get(key, []))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        pass


class FakeEngine:
    def __init__(self, rows_by_sql=None):
        self.conn = FakeConn(rows_by_sql)

    def connect(self):
        return self.conn


def _mk_row(**kw):
    class R(dict):
        @property
        def _mapping(self):
            return self
    return R(**kw)


def _patch_engine(monkeypatch, rows_by_sql=None):
    fake = FakeEngine(rows_by_sql)
    monkeypatch.setattr(eval_run_repo, "get_control_engine", lambda: fake)
    return fake


def test_insert_and_list(monkeypatch):
    fake = _patch_engine(monkeypatch, {"SELECT": [
        _mk_row(id=1, created_at="2026-08-14 16:00:00", scenario="SCN-001",
                rounds=3, success_rate=0.667, avg_duration_ms=45000,
                total_cost=0.02, model_snapshot="qwen3.8-max")]})
    rid = eval_run_repo.insert_eval_run(
        scenario="SCN-001", rounds=3, success_rate=0.667,
        avg_duration_ms=45000, total_cost=0.02, model_snapshot="qwen3.8-max",
        summary="2/3 recovered", raw_json='{"rounds":[]}')
    assert rid == 42
    assert "INSERT INTO eval_run" in fake.conn.last_sql
    rows = eval_run_repo.list_eval_runs()
    assert len(rows) == 1
    assert rows[0]["scenario"] == "SCN-001"
    assert rows[0]["success_rate"] == pytest.approx(0.667)


def test_get_eval_run_detail(monkeypatch):
    _patch_engine(monkeypatch, {"SELECT": [
        _mk_row(id=7, created_at="2026-08-14 16:00:00", scenario="SCN-002",
                rounds=1, success_rate=1.0, avg_duration_ms=30000,
                total_cost=0.01, model_snapshot="qwen3.7-flash",
                summary="recovered", raw_json='{"rounds":[{"round":1}]}')]})
    row = eval_run_repo.get_eval_run(7)
    assert row is not None
    assert row["id"] == 7
    assert "rounds" in row["raw_json"]


def test_get_missing_returns_none(monkeypatch):
    _patch_engine(monkeypatch, {"SELECT": []})
    assert eval_run_repo.get_eval_run(999999) is None
