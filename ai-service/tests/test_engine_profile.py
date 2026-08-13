"""engine 按 Run Profile 隔离:offline_eval 禁 DB;executor 用独立 fix_executor URL。"""
import pytest


def test_offline_eval_disables_all_engines(monkeypatch):
    monkeypatch.setenv("TRACEMIND_RUN_PROFILE", "offline_eval")
    monkeypatch.setenv("TRACEMIND_LLM_MODE", "fake")
    from app.config import Settings, DATABASE_ACCESS_DISABLED
    from app.db import engine as engine_mod
    monkeypatch.setattr(engine_mod, "settings", Settings(_env_file=None))
    engine_mod.get_control_engine.cache_clear()
    engine_mod.get_readonly_engine.cache_clear()
    engine_mod.get_executor_engine.cache_clear()
    for getter in (engine_mod.get_control_engine, engine_mod.get_readonly_engine,
                   engine_mod.get_terminator_engine, engine_mod.get_executor_engine):
        with pytest.raises(DATABASE_ACCESS_DISABLED):
            getter()


def test_executor_uses_fix_executor_url(monkeypatch):
    monkeypatch.setenv("TRACEMIND_RUN_PROFILE", "ci_db")
    monkeypatch.setenv("TRACEMIND_LLM_MODE", "fake")
    monkeypatch.setenv("TRACEMIND_CONTROL_DB_URL", "mysql+pymysql://u:p@h:3306/db")
    monkeypatch.setenv("TRACEMIND_READONLY_DB_URL", "mysql+pymysql://u:p@h:3306/db")
    monkeypatch.setenv("TRACEMIND_SESSION_TERMINATOR_DB_URL", "mysql+pymysql://u:p@h:3306/")
    monkeypatch.setenv("TRACEMIND_FIX_EXECUTOR_DB_URL", "mysql+pymysql://fix:pwd@h:3306/db")
    import app.config as cfg
    from app.config import Settings
    from app.db import engine as engine_mod
    s = Settings(_env_file=None)
    monkeypatch.setattr(engine_mod, "settings", s)  # 替换 engine 模块内的引用
    engine_mod.get_executor_engine.cache_clear()
    e = engine_mod.get_executor_engine()
    assert e.url.username == "fix"  # SQLAlchemy str(url) 会脱敏密码,断言用户名
    assert e.url.database == "db"
    engine_mod.get_executor_engine.cache_clear()


def test_ci_db_missing_fix_executor_raises(monkeypatch):
    monkeypatch.setenv("TRACEMIND_RUN_PROFILE", "ci_db")
    monkeypatch.setenv("TRACEMIND_LLM_MODE", "fake")
    monkeypatch.setenv("TRACEMIND_CONTROL_DB_URL", "mysql+pymysql://u:p@h:3306/db")
    monkeypatch.setenv("TRACEMIND_READONLY_DB_URL", "mysql+pymysql://u:p@h:3306/db")
    monkeypatch.setenv("TRACEMIND_SESSION_TERMINATOR_DB_URL", "mysql+pymysql://u:p@h:3306/")
    monkeypatch.setenv("TRACEMIND_FIX_EXECUTOR_DB_URL", "mysql+pymysql://fix:pwd@h:3306/db")
    import app.config as cfg
    from app.config import Settings
    from app.db import engine as engine_mod
    s = Settings(_env_file=None)
    monkeypatch.setattr(engine_mod, "settings", s)
    # 构造 fix_executor_url 为空的副本,模拟缺失
    s2 = Settings(_env_file=None)
    object.__setattr__(s2, "fix_executor_db_url", "")
    monkeypatch.setattr(engine_mod, "settings", s2)
    engine_mod.get_executor_engine.cache_clear()
    with pytest.raises(ValueError, match="TRACEMIND_FIX_EXECUTOR_DB_URL"):
        engine_mod.get_executor_engine()
    engine_mod.get_executor_engine.cache_clear()
