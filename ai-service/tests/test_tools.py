from app.tools.execute import execute_tool


def test_get_service_metrics_ok(monkeypatch):
    """V1.4:默认 fixture 后端返回稳定结构(fixture 值 p95Ms=2)。"""
    out = execute_tool("get_service_metrics", incident_id=None,
                       service_ref="inventory-service", window_seconds=300)
    assert out["success"] is True
    assert out["data"]["sourceBackend"] == "fixture"
    assert out["data"]["p95Ms"] == 2


def test_get_query_plan_rejects_unknown_ref():
    out = execute_tool("get_query_plan", incident_id=None,
                       query_ref="DROP_TABLES", sample_parameters={"skuId": 1})
    assert out["success"] is False
    assert out["error_code"] in ("UNKNOWN_QUERY_REF", "VALIDATION_ERROR")


def test_get_index_info_returns_indexes():
    out = execute_tool("get_index_info", incident_id=None, table_ref="inventory")
    assert out["success"] is True
    assert isinstance(out["data"]["indexes"], list)
    names = [i["index_name"] for i in out["data"]["indexes"]]
    assert "PRIMARY" in names  # PRIMARY 始终存在,不依赖故障状态


def test_execute_tool_validates_params():
    out = execute_tool("get_service_metrics", incident_id=None,
                       service_ref="evil-service", window_seconds=300)
    assert out["success"] is False
    assert out["error_code"] in ("VALIDATION_ERROR", "TOOL_ERROR")
