"""V1.12 动态路由:ModelScorer 按 (node, model) 滑动窗口维护加权评分。"""
from collections import deque

MIN_SAMPLES = 5  # 窗口数据少于该值视为冷启动,返回 None


class ModelScorer:
    def __init__(self, window: int = 20,
                 weights: tuple[float, float, float] = (0.6, 0.25, 0.15)):
        self.window = window
        self.w1, self.w2, self.w3 = weights
        self._windows: dict[tuple[str, str], deque] = {}

    def update(self, node: str, model: str, outcome: dict) -> None:
        key = (node, model)
        q = self._windows.setdefault(key, deque(maxlen=self.window))
        q.append({"success": bool(outcome.get("success")),
                  "latency_ms": outcome.get("latency_ms") or 0,
                  "cost": outcome.get("cost") or 0.0})

    def best(self, node: str, candidates: list[str]) -> str | None:
        """候选里选窗口评分最高者;数据不足(< MIN_SAMPLES)返回 None(调用方回落默认)。"""
        scored = []
        for m in candidates:
            q = self._windows.get((node, m))
            if q is None or len(q) < MIN_SAMPLES:
                continue
            scored.append((self._score(q), m))
        if not scored:
            return None
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    def _score(self, q: deque) -> float:
        n = len(q)
        success = sum(1 for s in q if s["success"])
        success_rate = success / n
        avg_latency = sum(s["latency_ms"] for s in q) / n
        avg_cost = sum(s["cost"] for s in q) / n
        latency_norm = min(1.0, avg_latency / 1000.0)     # 1s 视为最差,clamp
        cost_norm = min(1.0, avg_cost / 0.1)              # 0.1 元视为最差,clamp
        return (self.w1 * success_rate
                + self.w2 * (1 - latency_norm)
                + self.w3 * (1 - cost_norm))
