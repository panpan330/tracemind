"""百炼 text-embedding 向量化(显式 dimensions)。"""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class Embedder:
    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, dimensions: int | None = None,
                 timeout: float = 20.0) -> None:
        self.base_url = (base_url or settings.embedding_base_url).rstrip("/")
        self.api_key = api_key or settings.embedding_api_key
        self.model = model or settings.embedding_model
        self.dimensions = dimensions or settings.embedding_dimensions
        self.timeout = timeout

    def embed(self, text: str) -> list[float] | None:
        try:
            resp = httpx.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": text, "dimensions": self.dimensions},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            vec = resp.json()["data"][0]["embedding"]
            if len(vec) != self.dimensions:
                logger.warning("embedding 维度不符: 期望 %d 实际 %d", self.dimensions, len(vec))
                return None
            return vec
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            logger.warning("embedding 调用失败: %s", exc)
            return None
