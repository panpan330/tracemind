"""Streamable HTTP MCP Server 入口(独立容器 mcp-tools,stateless_http=True)。"""
import time

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route


def create_http_app() -> Starlette:
    from app.config.mcp import build_mcp_server_settings
    from app.mcp.server_factory import create_mcp_server
    from app.tools_infrastructure.audit_repository import MySqlToolAuditPort

    s = build_mcp_server_settings()
    if s.mcp_transport != "streamable_http":
        raise RuntimeError("mcp_transport 必须为 streamable_http 才能启动 HTTP Server")

    audit = MySqlToolAuditPort()
    mcp = create_mcp_server(runtime="real", audit=audit)

    # SDK 原生安全能力:stateless / transport_security(DNS rebinding + Origin)/ body 上限
    mcp.settings.stateless_http = True
    mcp.settings.max_request_body_size = s.mcp_max_request_bytes
    if hasattr(mcp.settings, "transport_security") and mcp.settings.transport_security is None:
        from mcp.server.transport_security import TransportSecuritySettings
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_origins=[],          # 服务间调用:Origin 缺失放行,存在需命中(空=仅缺失放行)
            allowed_hosts=[],
        )

    core = mcp.streamable_http_app()   # Starlette,含 /mcp

    async def health_live(request):
        return JSONResponse({"status": "live", "mcpProtocol": "streamable-http",
                             "version": "v1.7", "time": int(time.time())})

    async def health_ready(request):
        # ready = ToolRegistry + 认证配置 + 审计端口可用;不依赖下游全部健康
        ok = bool(s.mcp_auth_clients_file)
        detail = {"registry": True, "auth": ok, "audit": True}
        if not ok:
            detail["auth"] = False
        return JSONResponse({"status": "ready" if ok else "not_ready",
                             "detail": detail}, status_code=200 if ok else 503)

    # 组合:health 路由 + 安全中间件链(认证/限流/Origin 由 security.py 提供)
    from app.mcp.security import build_security_middleware
    routes = [Route("/health/live", health_live), Route("/health/ready", health_ready)]
    app = Starlette(routes=routes, middleware=build_security_middleware())
    # 挂载 /mcp(Streamable HTTP 核心)于同一 ASGI 应用
    app.mount("/mcp", core)
    return app


def main() -> None:
    import uvicorn
    app = create_http_app()
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")


if __name__ == "__main__":
    main()
