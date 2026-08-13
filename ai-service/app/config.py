"""app.config 薄壳:兼容既有 `from app.config import settings`。"""
from app.config.settings import Settings, settings  # noqa: F401
