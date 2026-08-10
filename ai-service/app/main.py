from fastapi import FastAPI

# 先触发工具注册(app.tools.__init__ 填充 TOOL_REGISTRY),再定义 app 实例
import app.tools  # noqa: E402,F401

from app.api import demo, incidents, runs  # noqa: E402

app = FastAPI(title="TraceMind AI Service")


@app.get("/api/health")
def health():
    return {"status": "ok"}


app.include_router(incidents.router)
app.include_router(runs.router)
app.include_router(demo.router)
