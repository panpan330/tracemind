"""get_index_info handler:经 IndexPort 端口获取数据(不含业务实现)。"""
from app.tools_core.errors import ToolBusinessError


from app.tools_core.ports import IndexPort

def build(ports: dict) -> dict:
    ix = ports.get("index")

    def get_index_info(table_ref: str) -> dict:
        if ix is None:
            raise ToolBusinessError("PORT_UNAVAILABLE", "index 端口未配置", retryable=False)
        try:
            return ix.get_index_info(table_ref)
        except ToolBusinessError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ToolBusinessError("INDEX_QUERY_FAILED", str(e), retryable=True) from e

    return {"get_index_info": get_index_info}
