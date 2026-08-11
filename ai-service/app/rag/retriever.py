"""Retriever:冷却退避 + 单实例锁 + 健康状态(healthy/degraded/probing)。"""
import logging
import threading
import time

from app.rag.runbook_store import RagUnavailableError

logger = logging.getLogger(__name__)


class Retriever:
    BASE_COOLDOWN = 60.0
    MAX_COOLDOWN = 600.0

    def __init__(self, store, on_recovered=None, cooldown_seconds: float = BASE_COOLDOWN) -> None:
        self.store = store
        self.on_recovered = on_recovered
        self.base_cooldown = cooldown_seconds
        self.cooldown = cooldown_seconds
        self.degraded = False
        self._next_probe = 0.0
        self._failures = 0
        # threading.Lock:图在 to_thread 线程池运行,同步锁即可防并发探活
        self._lock = threading.Lock()

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        if self.degraded and time.monotonic() < self._next_probe:
            return []
        # 防并发探活:同一次健康探测只允许一个执行者
        if not self._lock.locked():
            with self._lock:
                return self._search_once(query, top_k)
        return self._search_once(query, top_k) if not self.degraded else []

    def _search_once(self, query: str, top_k: int) -> list[dict]:
        try:
            hits = self.store.search(query, top_k=top_k)
            if self.degraded:
                logger.info("RAG 已恢复")
                self.degraded = False
                self._failures = 0
                self.cooldown = self.base_cooldown
                if self.on_recovered:
                    self.on_recovered()
            return hits
        except RagUnavailableError as exc:
            self._failures += 1
            self.degraded = True
            backoff = min(self.base_cooldown * (2 ** (self._failures - 1)), self.MAX_COOLDOWN)
            self.cooldown = backoff
            self._next_probe = time.monotonic() + backoff
            logger.warning("RAG 检索失败(第 %d 次),%.0fs 后重试: %s", self._failures, backoff, exc)
            return []
