# ai-service/tests/test_migration_013.py
"""V2.1-D migration 013 内容门禁:fix_execution 审计列补齐(004 旧结构曾遮蔽 006)。"""
from pathlib import Path


def _sql() -> str:
    return Path("../scripts/db/migrations/013_v21d_fix_execution_audit.sql").read_text(encoding="utf-8")


def test_migration_013_adds_audit_columns():
    sql = _sql()
    for token in ("blocking_relation_hash", "execution_result", "kill_attempted",
                  "actual_processlist_id", "started_at", "finished_at"):
        assert token in sql, f"013 缺少 {token}"


def test_migration_013_relaxes_nullable_columns():
    sql = _sql()
    # 006 设计意图:proposal/approval 可空(repo 允许 None)
    assert "MODIFY COLUMN fix_proposal_id BIGINT NULL" in sql
    assert "MODIFY COLUMN approval_id     BIGINT NULL" in sql
    assert "MODIFY COLUMN status          VARCHAR(32)" in sql


def test_migration_013_no_destructive_ops():
    sql = _sql()
    upper = sql.upper()
    assert "DROP COLUMN" not in upper, "013 不得删除列(不丢审计数据)"
    assert "DROP TABLE" not in upper
    assert "TRUNCATE" not in upper
