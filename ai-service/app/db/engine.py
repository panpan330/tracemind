from functools import lru_cache

from sqlalchemy import Engine, create_engine

from app.config import settings


@lru_cache
def get_control_engine() -> Engine:
    """tracemind_control_app:控制库 CRUD,普通 Incident/审计代码使用。"""
    return create_engine(settings.control_db_url, pool_pre_ping=True)


@lru_cache
def get_readonly_engine() -> Engine:
    """ai_investigator:只读业务表 + performance_schema + information_schema。"""
    return create_engine(settings.readonly_db_url, pool_pre_ping=True)


@lru_cache
def get_executor_engine() -> Engine:
    """fix_executor:仅目标表 INDEX 权限,只允许 execute_fix Action 使用。"""
    executor_url = settings.control_db_url.replace(
        "tracemind_control_app:control_app_pwd", "fix_executor:fix_executor_pwd"
    ).replace("tracemind_control", "tracemind_business")
    return create_engine(executor_url, pool_pre_ping=True)
