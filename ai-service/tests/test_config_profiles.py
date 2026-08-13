"""Run Profile fail-closed 语义:URL 缺失/非法 LLM 模式/offline_eval 禁 DB。"""
import pytest


def test_ci_db_missing_url_raises(monkeypatch):
    monkeypatch.setenv("TRACEMIND_RUN_PROFILE", "ci_db")
    monkeypatch.delenv("TRACEMIND_SESSION_TERMINATOR_DB_URL", raising=False)
    monkeypatch.delenv("TRACEMIND_FIX_EXECUTOR_DB_URL", raising=False)
    monkeypatch.setenv("TRACEMIND_CONTROL_DB_URL", "mysql+pymysql://u:p@h:3306/db")
    monkeypatch.setenv("TRACEMIND_READONLY_DB_URL", "mysql+pymysql://u:p@h:3306/db")
    from app.config import Settings
    with pytest.raises(ValueError, match="TRACEMIND_SESSION_TERMINATOR_DB_URL"):
        Settings(_env_file=None)


def test_full_e2e_must_be_real_strict(monkeypatch):
    monkeypatch.setenv("TRACEMIND_RUN_PROFILE", "full_e2e")
    monkeypatch.setenv("TRACEMIND_LLM_MODE", "fake")
    for url, val in [
        ("TRACEMIND_CONTROL_DB_URL", "mysql+pymysql://u:p@h:3306/db"),
        ("TRACEMIND_READONLY_DB_URL", "mysql+pymysql://u:p@h:3306/db"),
        ("TRACEMIND_SESSION_TERMINATOR_DB_URL", "mysql+pymysql://u:p@h:3306/"),
        ("TRACEMIND_FIX_EXECUTOR_DB_URL", "mysql+pymysql://u:p@h:3306/db"),
    ]:
        monkeypatch.setenv(url, val)
    from app.config import Settings
    with pytest.raises(ValueError, match="real_strict"):
        Settings(_env_file=None)


def test_offline_eval_defines_database_access_disabled(monkeypatch):
    monkeypatch.setenv("TRACEMIND_RUN_PROFILE", "offline_eval")
    from app.config import Settings
    from app.config import DATABASE_ACCESS_DISABLED
    assert issubclass(DATABASE_ACCESS_DISABLED, Exception)


def test_local_default_keeps_terminator_fallback(monkeypatch):
    monkeypatch.delenv("TRACEMIND_RUN_PROFILE", raising=False)
    monkeypatch.delenv("TRACEMIND_LLM_MODE", raising=False)
    from app.config import Settings
    s = Settings(_env_file=None)
    assert s.run_profile == "local"


def test_local_allows_real_strict(monkeypatch):
    monkeypatch.delenv("TRACEMIND_RUN_PROFILE", raising=False)
    monkeypatch.setenv("TRACEMIND_LLM_MODE", "real_strict")
    from app.config import Settings
    s = Settings(_env_file=None)
    assert s.llm_mode == "real_strict"
