"""MCP/JSON-RPC 协议层错误(不混入业务/基础设施错误)。"""


class ProtocolError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(f"[{code}] {message}")
        self.code = code


MCP_PROTOCOL_VERSION_UNSUPPORTED = "MCP_PROTOCOL_VERSION_UNSUPPORTED"
MCP_HEADER_BODY_MISMATCH = "MCP_HEADER_BODY_MISMATCH"
MCP_JSONRPC_INVALID = "MCP_JSONRPC_INVALID"
MCP_TOOL_NOT_FOUND = "MCP_TOOL_NOT_FOUND"
MCP_SCHEMA_MISMATCH = "MCP_SCHEMA_MISMATCH"
