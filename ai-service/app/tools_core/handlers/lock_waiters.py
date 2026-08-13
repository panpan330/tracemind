"""get_lock_waiters / get_transaction_details handler:经 LockPort 端口获取数据(不含业务实现)。"""
from app.tools_core.errors import ToolBusinessError


from app.tools_core.ports import LockPort

def build(ports: dict) -> dict:
    lock = ports.get("lock")

    def get_lock_waiters(scope_ref: str) -> dict:
        if lock is None:
            raise ToolBusinessError("PORT_UNAVAILABLE", "lock 端口未配置", retryable=False)
        try:
            return lock.get_lock_waiters(scope_ref)
        except ToolBusinessError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ToolBusinessError("LOCK_QUERY_FAILED", str(e), retryable=True) from e

    def get_transaction_details(transaction_ref: str) -> dict:
        if lock is None:
            raise ToolBusinessError("PORT_UNAVAILABLE", "lock 端口未配置", retryable=False)
        try:
            return lock.get_transaction_details(transaction_ref)
        except ToolBusinessError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ToolBusinessError("TRX_QUERY_FAILED", str(e), retryable=True) from e

    return {"get_lock_waiters": get_lock_waiters,
            "get_transaction_details": get_transaction_details}
