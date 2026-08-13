# syntax=docker/dockerfile:1
# TraceMind ai-service 双 target:
#   runtime — 生产精简(不含测试/评测/临时密钥)
#   ci      — 含 pytest/fixture/评测数据(供 Full E2E 与 CI 使用)
# 构建(VM/CI):
#   DOCKER_BUILDKIT=0 docker build -t tracemind-ai:runtime --target runtime .
#   DOCKER_BUILDKIT=0 docker build -t tracemind-ai:ci --target ci .

FROM python:3.12-slim AS base
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# 依赖层(利用 Docker 缓存;国内镜像源加速,VM 直连 pypi.org 极慢)
COPY pyproject.toml ./
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ \
    fastapi>=0.115 \
    "uvicorn[standard]>=0.30" \
    "sqlalchemy>=2.0" \
    "pymysql>=1.1" \
    "httpx>=0.27" \
    "pydantic-settings>=2.3" \
    "langgraph>=0.2" \
    "langgraph-checkpoint-sqlite>=3.1" \
    "langchain-core>=0.3" \
    "mcp>=1.28,<2"

# ---- runtime:生产精简 ----
FROM base AS runtime
COPY app ./app
RUN mkdir -p /app/data && useradd -m appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
ENV TRACEMIND_CHECKPOINT_PATH=/app/data/checkpoints.sqlite
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---- ci:含测试/覆盖率(供 Full E2E 与 CI;默认 Profile 为 ci_db/fake)----
# 注:scripts/db、scripts/ci、仓库根 data/ 评测集都在 build context(ai-service/)外,
#     由运行时挂载或从宿主执行;镜像只包含 ai-service 自身代码与测试。
FROM runtime AS ci
USER root
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ \
    pytest pytest-asyncio pytest-cov
COPY tests ./tests
USER appuser
ENV TRACEMIND_RUN_PROFILE=ci_db TRACEMIND_LLM_MODE=fake
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
