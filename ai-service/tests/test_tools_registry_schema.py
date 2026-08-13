# ai-service/tests/test_tools_registry_schema.py
from app.tools_core.registry import TOOL_REGISTRY, ToolSpec
from app.tools_core.schemas import ToolResult, GetServiceMetricsIn, SERVICE_REF_WHITELIST


def test_registry_types_importable():
    # registry 由 app/tools/__init__.py 填充;此处只验证模块可导入与类型存在
    assert ToolSpec is not None and ToolResult is not None
    assert isinstance(TOOL_REGISTRY, dict)


def test_whitelist_importable_from_tools_core():
    assert SERVICE_REF_WHITELIST == {"order-service", "inventory-service"}


def test_old_path_removed():
    import importlib
    try:
        importlib.import_module("app.tools.registry")
        assert False, "app.tools.registry 应已迁移到 tools_core"
    except ModuleNotFoundError:
        pass
