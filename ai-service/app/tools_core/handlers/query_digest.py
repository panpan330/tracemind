"""list_expensive_query_digests handler:经 DigestPort 端口获取数据(不含业务实现)。"""
from app.tools_core.errors import ToolBusinessError


from app.tools_core.ports import DigestPort

def build(ports: dict) -> dict:
    d = ports.get("digest")

    def list_expensive_query_digests(window_seconds: int | None = None) -> dict:
        if d is None:
            raise ToolBusinessError("PORT_UNAVAILABLE", "digest 端口未配置", retryable=False)
        try:
            return d.list_expensive_digests(window_seconds)
        except ToolBusinessError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ToolBusinessError("DIGEST_QUERY_FAILED", str(e), retryable=True) from e

    return {"list_expensive_query_digests": list_expensive_query_digests}
