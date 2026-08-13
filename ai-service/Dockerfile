# syntax=docker/dockerfile:1
# TraceMind ai-service 双 target:
#   ai-runtime         — AI 控制服务(LLM/Agent 全依赖)
#   mcp-tools-runtime  — 独立 MCP 工具服务(不含 LLM/Agent 运行依赖)
# 构建(VM):
#   DOCKER_BUILDKIT=0 docker build -t tracemind-ai-service:<sha> --target ai-runtime .
#   DOCKER_BUILDKIT=0 docker build -t tracemind-mcp-tools:<sha> --target mcp-tools-runtime .

FROM python:3.12-slim AS base
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# 公共依赖层(两 target 共享;利用 Docker 缓存 + 国内镜像源)
COPY pyproject.toml ./
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ \
    fastapi>=0.115 "uvicorn[standard]>=0.30" "sqlalchemy>=2.0" "pymysql>=1.1" \
    "httpx>=0.27" "pydantic-settings>=2.3" "mcp>=1.28,<2"

# ---- ai-runtime:AI 控制服务(含 LLM/Agent 依赖)----
FROM base AS ai-runtime
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ \
    "langgraph>=0.2" "langgraph-checkpoint-sqlite>=3.1" "langchain-core>=0.3"
COPY app ./app
RUN mkdir -p /app/data && useradd -m appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
ENV TRACEMIND_CHECKPOINT_PATH=/app/data/checkpoints.sqlite
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---- mcp-tools-runtime:独立 MCP 工具服务(不装 LLM/Agent 依赖)----
FROM base AS mcp-tools-runtime
COPY app ./app
RUN mkdir -p /app/data && useradd -m appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8001
CMD ["python", "-m", "app.mcp.server_http"]

# ---- ci:含测试/覆盖率(本地/手动回归用)----
FROM ai-runtime AS ci
USER root
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ \
    pytest pytest-asyncio pytest-cov
COPY tests ./tests
USER appuser
ENV TRACEMIND_RUN_PROFILE=local TRACEMIND_LLM_MODE=fake
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
