"""MCP HTTP 安全:Opaque Token 认证(client_id 从认证派生)+ Origin 校验(限流见 build_security_middleware)。"""
import hashlib
import json
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.tools_core.context import AuthenticatedPrincipal


def fingerprint(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()


def load_clients(file_path: Optional[str]) -> dict:
    """Token Fingerprint → {subject, audience, scopes}。"""
    if not file_path:
        return {}
    with open(file_path, encoding="utf-8") as f:
        return json.load(f)


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, clients: dict):
        super().__init__(app)
        self._clients = clients

    async def dispatch(self, request, call_next):
        # Origin 校验:存在必须命中精确 Allowlist;缺失 → 认证后放行(服务间调用)
        origin = request.headers.get("origin")
        if origin is not None and "__origins__" in self._clients \
                and origin not in self._clients["__origins__"]:
            return JSONResponse({"error": "origin_rejected"}, status_code=403)
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        token = auth[len("Bearer "):].strip()
        fp = fingerprint(token)
        entry = self._clients.get(fp)
        if not entry:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        request.state.principal = AuthenticatedPrincipal(
            client_id=entry["subject"], subject=entry["subject"],
            audience=entry["audience"], scopes=entry["scopes"], token_fingerprint=fp)
        return await call_next(request)


def build_security_middleware(clients_file: Optional[str] = None) -> list:
    """starlette Middleware 列表:认证 + Origin(限流见注释,单实例 in-process 计数可后续加)。"""
    from starlette.middleware import Middleware
    clients = load_clients(clients_file)
    return [Middleware(AuthMiddleware, clients=clients)]
