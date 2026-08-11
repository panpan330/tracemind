import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

# 先触发工具注册(app.tools.__init__ 填充 TOOL_REGISTRY),再定义 app 实例
import app.tools  # noqa: E402,F401

from app.api import approvals, demo, incidents, runs, stream  # noqa: E402
from app.mcp.client import McpClientManager  # noqa: E402
from app.services import runner  # noqa: E402
from app.services.approval_scanner import scanner_loop  # noqa: E402

mcp_manager: McpClientManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global mcp_manager
    # MCP Server 启动/契约校验失败 → start() 抛异常 → 应用启动失败(readiness=false)
    mcp_manager = McpClientManager()
    await mcp_manager.start()
    await runner.recover_pending_runs()  # 启动先恢复未完成任务,再接收流量
    task = asyncio.create_task(scanner_loop())
    yield
    task.cancel()
    await mcp_manager.stop()
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
