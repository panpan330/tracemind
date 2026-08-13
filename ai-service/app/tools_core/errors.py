"""工具业务层错误:code 为受控错误码,retryable 供 Client 决定是否重试。"""


class ToolBusinessError(Exception):
    def __init__(self, code: str, message: str = "", retryable: bool = False) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
