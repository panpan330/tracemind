"""Qdrant REST 客户端(Collection Alias 查询)。"""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class RagUnavailableError(Exception):
    pass


class RunbookStore:
    def __init__(self, embedder, base_url: str | None = None,
                 read_api_key: str | None = None,
                 collection_alias: str | None = None, timeout: float = 10.0) -> None:
        self.embedder = embedder
        self.base_url = (base_url or settings.qdrant_url).rstrip("/")
        self.read_api_key = (read_api_key if read_api_key is not None
                             else settings.qdrant_read_api_key)
        self.collection = (collection_alias or settings.qdrant_collection_alias)
        self.timeout = timeout

    def _headers(self, write: bool = False) -> dict:
        key = (settings.qdrant_write_api_key if write else self.read_api_key)
        return {"X-API-Key": key} if key else {}

    def ensure_collection(self, dim: int) -> None:
        try:
            resp = httpx.get(f"{self.base_url}/collections/{self.collection}",
                             headers=self._headers(), timeout=self.timeout)
            if resp.status_code == 200:
                actual = resp.json()["result"]["config"]["params"]["vectors"]["size"]
                dist = resp.json()["result"]["config"]["params"]["vectors"]["distance"]
                if actual != dim or dist != "Cosine":
                    raise RagUnavailableError(
                        f"collection 配置不符: size={actual} dist={dist}(期望 {dim}/Cosine)")
                return
            if resp.status_code != 404:
                raise RagUnavailableError(f"Qdrant 检查失败: HTTP {resp.status_code}")
            put = httpx.put(f"{self.base_url}/collections/{self.collection}",
                            headers=self._headers(write=True),
                            json={"vectors": {"size": dim, "distance": "Cosine"}},
                            timeout=self.timeout)
            put.raise_for_status()
        except httpx.HTTPError as exc:
            raise RagUnavailableError(f"Qdrant 不可用: {exc}") from exc

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        vector = self.embedder.embed(query)
        if vector is None:
            raise RagUnavailableError("embedding 失败,无法检索")
        try:
            resp = httpx.post(f"{self.base_url}/collections/{self.collection}/points/search",
                              headers=self._headers(),
                              json={"vector": vector, "limit": top_k, "with_payload": True},
                              timeout=self.timeout)
            resp.raise_for_status()
            points = resp.json()["result"]["points"]
            return [{"text": p["payload"].get("text", ""), "score": p.get("score", 0.0),
                     "doc_id": p["payload"].get("doc_id", ""),
                     "title": p["payload"].get("title", "")}
                    for p in points]
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            raise RagUnavailableError(f"Qdrant search 失败: {exc}") from exc

    def upsert(self, point_id: int, vector: list[float], payload: dict) -> None:
        try:
            resp = httpx.post(f"{self.base_url}/collections/{self.collection}/points?wait=true",
                              headers=self._headers(write=True),
                              json={"points": [{"id": point_id, "vector": vector,
                                                "payload": payload}]},
                              timeout=self.timeout)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RagUnavailableError(f"Qdrant upsert 失败: {exc}") from exc

    def delete_filter(self, doc_id: str) -> None:
        try:
            resp = httpx.post(f"{self.base_url}/collections/{self.collection}/points/delete?wait=true",
                              headers=self._headers(write=True),
                              json={"filter": {"must": [{"key": "doc_id",
                                                         "match": {"value": doc_id}}]}},
                              timeout=self.timeout)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RagUnavailableError(f"Qdrant delete 失败: {exc}") from exc

    def count(self) -> int:
        try:
            resp = httpx.get(f"{self.base_url}/collections/{self.collection}",
                             headers=self._headers(), timeout=self.timeout)
            resp.raise_for_status()
            return int(resp.json()["result"]["points_count"])
        except (httpx.HTTPError, KeyError) as exc:
            raise RagUnavailableError(f"Qdrant count 失败: {exc}") from exc
