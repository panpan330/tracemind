FROM python:3.12-slim

WORKDIR /app

# 依赖层(利用 Docker 缓存)
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    fastapi>=0.115 \
    "uvicorn[standard]>=0.30" \
    "sqlalchemy>=2.0" \
    "pymysql>=1.1" \
    "httpx>=0.27" \
    "pydantic-settings>=2.3" \
    "langgraph>=0.2" \
    "langchain-core>=0.3"

# 应用代码
COPY app ./app
RUN mkdir -p /app/data && useradd -m appuser && chown -R appuser:appuser /app

USER appuser
EXPOSE 8000

ENV TRACEMIND_CHECKPOINT_PATH=/app/data/checkpoints.sqlite
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
