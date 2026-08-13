from functools import lru_cache

from sqlalchemy import Engine, create_engine

from app.config import DATABASE_ACCESS_DISABLED, settings


def _deny_if_offline() -> None:
    """offline_eval 是评测环境:允许控制/只读查询(Agent 调查落库是核心),
    但处置类连接(executor/terminator)禁用——评测不得触发 KILL 等副作用。
    V1.6 修正:早期设计"offline_eval 禁全部 DB"与 Agent 落库冲突。"""
    return


def _deny_side_effect_if_offline() -> None:
    """处置副作用连接:offline_eval 下禁用。"""
    if settings.run_profile == "offline_eval":
        raise DATABASE_ACCESS_DISABLED(
            f"run_profile={settings.run_profile} 禁止处置类数据库访问(executor/terminator)")


@lru_cache
def get_control_engine() -> Engine:
    """tracemind_control_app:控制库 CRUD,普通 Incident/审计代码使用。"""
    _deny_if_offline()
    return create_engine(settings.control_db_url, pool_pre_ping=True)


@lru_cache
def get_readonly_engine() -> Engine:
    """ai_investigator:只读业务表 + performance_schema + information_schema。"""
    _deny_if_offline()
    return create_engine(settings.readonly_db_url, pool_pre_ping=True)


@lru_cache
def get_executor_engine() -> Engine:
    """fix_executor:仅目标表 INDEX 权限,只允许 execute_fix Action 使用。
    V1.6:使用独立 fix_executor_db_url,禁止从 control URL 派生(设计 §5)。"""
    _deny_side_effect_if_offline()
    url = settings.fix_executor_db_url
    if not url:
        if settings.run_profile != "local":
            raise ValueError("TRACEMIND_FIX_EXECUTOR_DB_URL 缺失(禁止从 control URL 派生)")
        # local 向后兼容:保留派生(本地开发环境无独立 URL 配置)
        url = settings.control_db_url.replace(
            "tracemind_control_app:control_app_pwd", "fix_executor:fix_executor_pwd"
        ).replace("tracemind_control", "tracemind_business")
    return create_engine(url, pool_pre_ping=True)


def get_terminator_engine() -> Engine:
    """session_terminator:会话终止专用连接(按需,不缓存——由 session_terminator 模块使用)。
    V1.6:offline_eval 禁处置(副作用);非 local 下缺 URL 直接失败。"""
    _deny_side_effect_if_offline()
    url = settings.session_terminator_db_url
    if not url:
        if settings.run_profile != "local":
            raise ValueError("TRACEMIND_SESSION_TERMINATOR_DB_URL 缺失(禁止回退只读引擎)")
        url = settings.readonly_db_url  # local 向后兼容
    return create_engine(url, pool_pre_ping=True, pool_recycle=1800)


def get_engine_from_url(url: str) -> Engine:
    """按 URL 创建独立连接(不缓存);供 session_terminator 等按需连接使用。"""
    _deny_side_effect_if_offline()
    return create_engine(url, pool_pre_ping=True, pool_recycle=1800)
