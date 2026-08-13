"""Client 侧基础设施错误 + retryable 判定。"""


class ClientError(Exception):
    def __init__(self, code: str, message: str = "", retryable: bool = False):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.retryable = retryable


MCP_CONNECT_FAILED = "MCP_CONNECT_FAILED"
MCP_REQUEST_TIMEOUT = "MCP_REQUEST_TIMEOUT"
MCP_DISCONNECTED = "MCP_DISCONNECTED"
MCP_RATE_LIMITED = "MCP_RATE_LIMITED"
MCP_AUTH_FAILED = "MCP_AUTH_FAILED"
MCP_ORIGIN_REJECTED = "MCP_ORIGIN_REJECTED"

# HTTP 状态 → retryable(429/502/503 可重试;401/403/400/404/413 不重试;504 按 outcome_unknown 处理)
HTTP_RETRYABLE = {400: False, 401: False, 403: False, 404: False, 413: False,
                  429: True, 500: False, 502: True, 503: True, 504: "outcome_unknown"}
