"""repo 读取方法:用 FakeEngine 验证 SQL 过滤 + 结果映射(不连真实库)。"""
from app.db import engine as db_engine


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class FakeConn:
    def __init__(self, rows_by_sql=None):
        self.rows_by_sql = rows_by_sql or {}
        self.last_sql = None
        self.last_params = None

    def execute(self, stmt, params=None):
        self.last_sql = str(stmt)
        self.last_params = params
        key = "SELECT" if "SELECT" in str(stmt).upper() else str(stmt)
        return FakeResult(self.rows_by_sql.get(key, []))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


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


def test_list_model_calls_by_run_filters_and_maps(monkeypatch):
    from app.repositories import model_call_repo
    fake = FakeEngine({"SELECT": [_mk_row(node="hypothesize", latency_ms=100,
                                          input_tokens=10, output_tokens=5)]})
    monkeypatch.setattr(model_call_repo, "get_control_engine", lambda: fake)
    rows = model_call_repo.list_model_calls_by_run(agent_run_id=7)
    assert len(rows) == 1
    assert rows[0]["node"] == "hypothesize"
    assert "agent_run_id" in fake.conn.last_sql
    assert fake.conn.last_params == {"r": 7}
