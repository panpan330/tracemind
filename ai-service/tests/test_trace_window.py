"""V1.7 真实 trace 闭环修复:incident.observed_at 缺失时窗口兜底。"""
import datetime

from app.services.trace_service import _resolve_incident_window


def test_resolve_window_defaults_to_search_window_when_observed_at_missing():
    """observed_at 缺失时,搜索窗口应退化为 max_trace_search_window_seconds,
    而不是 0 宽度(now~now)——否则 get_trace 永远查不到 trace。"""
    start, end = _resolve_incident_window({})
    sd = datetime.datetime.fromisoformat(str(start).replace("Z", "+00:00"))
    ed = datetime.datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    width = (ed - sd).total_seconds()
    assert width > 60, f"窗口宽度 {width}s,应退化为搜索窗口而非 0"


def test_resolve_window_uses_observed_at_when_present():
    import app.config as _c
    now = datetime.datetime.now(datetime.timezone.utc)
    obs = (now - datetime.timedelta(seconds=100)).isoformat()
    start, end = _resolve_incident_window({"observed_at": obs})
    sd = datetime.datetime.fromisoformat(str(start).replace("Z", "+00:00"))
    width = (datetime.datetime.fromisoformat(str(end).replace("Z", "+00:00")) - sd).total_seconds()
    assert 60 < width <= _c.settings.max_trace_search_window_seconds + 5
