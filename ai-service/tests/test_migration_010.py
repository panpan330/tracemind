# ai-service/tests/test_migration_010.py
"""V2.1-A migration 010 内容门禁:alert_event/alert_instance 结构与安全。"""
from pathlib import Path


def _sql() -> str:
    return Path("../scripts/db/migrations/010_v21_alert_gateway.sql").read_text(encoding="utf-8")


def test_migration_010_tables_and_constraints():
    sql = _sql()
    for token in ("alert_event", "alert_instance", "uk_alert_delivery",
                  "alert_instance_key", "delivery_hash", "current_status", "version"):
        assert token in sql, f"010 缺少 {token}"


def test_migration_010_delivery_unique_key():
    sql = _sql()
    assert "UNIQUE KEY uk_alert_delivery (source, delivery_hash)" in sql


def test_migration_010_no_password_in_sql():
    sql = _sql()
    assert "IDENTIFIED BY" not in sql.lower()
    assert "CREATE USER" not in sql.upper()
