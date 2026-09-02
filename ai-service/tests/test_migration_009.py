# ai-service/tests/test_migration_009.py
"""V2.0-A migration 009 内容门禁:追加式、无密码、唯一约束与快照列齐备。"""
from pathlib import Path


def _sql() -> str:
    return Path("../scripts/db/migrations/009_v20_run_baseline.sql").read_text(encoding="utf-8")


def test_migration_009_has_agent_run_snapshot_and_bindings():
    sql = _sql()
    for token in ("run_context_snapshot_json", "checkpoint_thread_id", "checkpoint_namespace",
                  "capability_bundle_version", "prompt_bundle_version", "tool_bundle_version",
                  "uk_agent_run_ckpt_thread"):
        assert token in sql, f"009 缺少 {token}"


def test_migration_009_binds_approval_to_run():
    sql = _sql()
    assert "agent_run_id" in sql
    assert "idx_approval_agent_run" in sql


def test_migration_009_explicit_proposal_action_type():
    sql = _sql()
    assert "action_type" in sql and "fix_proposal" in sql


def test_migration_009_backfills_legacy_rows():
    sql = _sql()
    assert "UPDATE agent_run SET checkpoint_thread_id = thread_id" in sql


def test_migration_009_no_password_in_sql():
    sql = _sql()
    assert "IDENTIFIED BY" not in sql.lower()
    assert "CREATE USER" not in sql.upper()
