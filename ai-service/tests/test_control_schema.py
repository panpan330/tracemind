from sqlalchemy import create_engine, inspect

from app.config import settings

engine = create_engine(settings.control_db_url)

EXPECTED_TABLES = {
    "incident", "agent_run", "hypothesis", "evidence", "hypothesis_evidence",
    "tool_call", "fix_definition", "fix_proposal", "approval", "fix_execution",
    "recovery_check", "postmortem", "incident_event",
}


def test_control_schema_tables_exist():
    tables = set(inspect(engine).get_table_names())
    missing = EXPECTED_TABLES - tables
    assert not missing, f"missing tables: {missing}"
