# ai-service/tests/test_migration_008.py
from pathlib import Path


def test_migration_008_has_tool_call_attempt():
    sql = Path("../scripts/db/migrations/008_tool_call_attempt.sql").read_text(encoding="utf-8")
    assert "tool_call_attempt" in sql
    assert "UNIQUE KEY" in sql or "UNIQUE (" in sql


def test_migration_008_no_password_in_sql():
    sql = Path("../scripts/db/migrations/008_tool_call_attempt.sql").read_text(encoding="utf-8")
    assert "IDENTIFIED BY" not in sql.lower()
