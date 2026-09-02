"""V2.0-A:时间语义统一回归 — 会话 UTC、审计写入应用侧 UTC、审批 CAS now_utc。"""
from datetime import datetime, timezone

from sqlalchemy import text

from app.db.engine import (UTC_SESSION_INIT_COMMAND, create_engine_utc,
                           get_control_engine)
from app.tools_infrastructure.audit_repository import _utcnow


def test_engine_session_time_zone_is_utc():
    """每个新连接会话 time_zone 必须为 +00:00(不受服务器默认时区影响)。"""
    with get_control_engine().connect() as conn:
        tz = conn.execute(text("SELECT @@session.time_zone")).scalar()
        assert tz in ("+00:00", "UTC"), f"会话时区非 UTC: {tz}"


def test_engine_now_matches_app_utc():
    """NOW()(DB 侧时间)与应用侧 naive UTC 一致(±5s),不存在 8 小时时差。"""
    app_now = datetime.now(timezone.utc).replace(tzinfo=None)
    with get_control_engine().connect() as conn:
        db_now = conn.execute(text("SELECT NOW()")).scalar()
    assert abs((db_now - app_now).total_seconds()) < 5


def test_create_engine_utc_sets_init_command():
    eng = create_engine_utc("sqlite://")
    assert eng is not None  # URL 层构造不炸;init_command 是 mysql 专用 connect_args
    assert "time_zone" in UTC_SESSION_INIT_COMMAND


def test_audit_utcnow_is_naive_utc():
    now = _utcnow()
    assert now.tzinfo is None  # 与 DB 存储语义一致(naive UTC)
    delta = abs((now - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds())
    assert delta < 5
