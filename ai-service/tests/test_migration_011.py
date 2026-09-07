# ai-service/tests/test_migration_011.py
"""V2.1-B migration 011 内容门禁:聚合列/关联表/调度列与安全。"""
from pathlib import Path


def _sql() -> str:
    return Path("../scripts/db/migrations/011_v21_aggregation.sql").read_text(encoding="utf-8")


def test_migration_011_incident_aggregation_columns():
    sql = _sql()
    for token in ("source", "alert_name", "environment", "alert_status",
                  "lifecycle_status", "group_key", "open_group_key", "first_seen_at",
                  "last_seen_at", "occurrence_count", "labels_json", "annotations_json",
                  "uk_incident_open_group"):
        assert token in sql, f"011 缺少 {token}"


def test_migration_011_agent_run_dispatch_columns():
    sql = _sql()
    for token in ("trigger_source", "active_run_key", "dispatch_status", "lease_owner",
                  "lease_until", "dispatch_attempts", "uk_agent_run_active"):
        assert token in sql, f"011 缺少 {token}"


def test_migration_011_incident_alert_authority():
    sql = _sql()
    assert "uk_incident_alert_instance" in sql      # 一实例至多属一 Incident(权威)
    assert "uk_incident_alert" in sql


def test_migration_011_no_password_in_sql():
    sql = _sql()
    assert "IDENTIFIED BY" not in sql.lower()
    assert "CREATE USER" not in sql.upper()
