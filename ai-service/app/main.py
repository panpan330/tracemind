import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

# 先触发工具注册(app.tools.__init__ 填充 TOOL_REGISTRY),再定义 app 实例
import app.tools  # noqa: E402,F401

from app.api import approvals, demo, incidents, replay, runs, stream  # noqa: E402
from app.mcp.client import McpClientManager, set_mcp_client  # noqa: E402
from app.services import runner  # noqa: E402
from app.services.approval_scanner import scanner_loop  # noqa: E402

mcp_manager: McpClientManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global mcp_manager
    # V1.7 fail-closed:vm_release/production 必须 streamable_http,禁止 stdio
    from app.config import settings
    profile = getattr(settings, "run_profile", "local")
    if profile in ("vm_release", "production") and settings.mcp_transport != "streamable_http":
        raise RuntimeError("vm_release/production 必须使用 mcp_transport=streamable_http,禁止 stdio")
    # MCP Server 启动/契约校验失败 → start() 抛异常 → 应用启动失败(readiness=false)
    mcp_manager = McpClientManager()
    await mcp_manager.start()
    set_mcp_client(mcp_manager)
    await runner.recover_pending_runs()  # 启动先恢复未完成任务,再接收流量
    task = asyncio.create_task(scanner_loop())
    yield
    task.cancel()
    await mcp_manager.stop()
    set_mcp_client(None)
    mcp_manager = None


app = FastAPI(title="TraceMind AI Service", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {"status": "ok", "mcp_ready": bool(mcp_manager and mcp_manager.is_ready)}


app.include_router(incidents.router)
app.include_router(runs.router)
app.include_router(approvals.router)
app.include_router(stream.router)
app.include_router(demo.router)
app.include_router(replay.router)
