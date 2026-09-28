# ai-service/tests/test_migration_012.py
"""V2.1-C migration 012 内容门禁:历史基线/episode 关闭/基线采集状态与安全。"""
from pathlib import Path


def _sql() -> str:
    return Path("../scripts/db/migrations/012_v21c_alert_closure.sql").read_text(encoding="utf-8")


def test_migration_012_incident_baseline_columns():
    sql = _sql()
    for token in ("baseline_window_start", "baseline_window_end",
                  "baseline_metrics_json", "baseline_quality",
                  "auto_run_started_at", "closed_at",
                  "current_health_snapshot_json"):
        assert token in sql, f"012 缺少 {token}"


def test_migration_012_agent_run_capture_status():
    sql = _sql()
    assert "baseline_capture_status" in sql
    # 封存终值只允许 OK/INSUFFICIENT;CAPTURE_FAILED 可被同 CAS 重试(注释钉死契约)
    assert "CAPTURE_FAILED" in sql


def test_migration_012_lifecycle_index():
    sql = _sql()
    assert "idx_incident_alert_lifecycle" in sql


def test_migration_012_no_password_in_sql():
    sql = _sql()
    assert "IDENTIFIED BY" not in sql.lower()
    assert "CREATE USER" not in sql.upper()
