import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

# 先触发工具注册(app.tools.__init__ 填充 TOOL_REGISTRY),再定义 app 实例
import app.tools  # noqa: E402,F401

from app.api import approvals, demo, incidents, runs, stream  # noqa: E402
from app.services.approval_scanner import scanner_loop  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(scanner_loop())
    yield
    task.cancel()


app = FastAPI(title="TraceMind AI Service", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {"status": "ok"}


app.include_router(incidents.router)
app.include_router(runs.router)
app.include_router(approvals.router)
app.include_router(stream.router)
app.include_router(demo.router)
