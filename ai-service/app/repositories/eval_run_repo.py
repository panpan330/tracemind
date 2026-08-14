"""eval_run 评测记录 repository(control 库)。"""
from sqlalchemy import text

from app.db.engine import get_control_engine

_EVAL_RUN_DDL = """
CREATE TABLE IF NOT EXISTS eval_run (
    id INT AUTO_INCREMENT PRIMARY KEY,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    scenario VARCHAR(64) NOT NULL,
    rounds INT NOT NULL DEFAULT 0,
    success_rate DECIMAL(5,4) NOT NULL DEFAULT 0,
    avg_duration_ms INT NOT NULL DEFAULT 0,
    total_cost DECIMAL(10,6) NOT NULL DEFAULT 0,
    model_snapshot VARCHAR(128) NOT NULL DEFAULT '',
    summary VARCHAR(255) NOT NULL DEFAULT '',
    raw_json TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _ensure_table() -> None:
    with get_control_engine().connect() as conn:
        conn.execute(text(_EVAL_RUN_DDL))
        conn.commit()


def insert_eval_run(*, scenario: str, rounds: int, success_rate: float,
                    avg_duration_ms: int, total_cost: float,
                    model_snapshot: str, summary: str, raw_json: str) -> int:
    _ensure_table()
    with get_control_engine().connect() as conn:
        result = conn.execute(text(
            "INSERT INTO eval_run (scenario, rounds, success_rate, avg_duration_ms, "
            "total_cost, model_snapshot, summary, raw_json) "
            "VALUES (:s, :r, :sr, :d, :c, :m, :sum, :raw)"),
            {"s": scenario, "r": rounds, "sr": success_rate, "d": avg_duration_ms,
             "c": total_cost, "m": model_snapshot, "sum": summary, "raw": raw_json})
        conn.commit()
        return result.lastrowid


def list_eval_runs() -> list[dict]:
    _ensure_table()
    with get_control_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT id, created_at, scenario, rounds, success_rate, avg_duration_ms, "
            "total_cost, model_snapshot FROM eval_run ORDER BY id DESC")).fetchall()
        return [dict(r._mapping) for r in rows]


def get_eval_run(eval_run_id: int) -> dict | None:
    _ensure_table()
    with get_control_engine().connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM eval_run WHERE id = :id"), {"id": eval_run_id}).fetchone()
        return dict(row._mapping) if row else None


def delete_all_for_test() -> None:
    with get_control_engine().connect() as conn:
        conn.execute(text("DELETE FROM eval_run"))
        conn.commit()
