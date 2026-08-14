"""V1.12 ModelScorer 单测:窗口滚动评分。"""
from app.agent.model_scorer import MIN_SAMPLES, ModelScorer


def _out(success=True, latency_ms=100, cost=0.001):
    return {"success": success, "latency_ms": latency_ms, "cost": cost}


def test_score_prefers_high_success():
    sc = ModelScorer()
    for _ in range(10):
        sc.update("select_tool", "flash", _out(True, 100, 0.001))      # 全成功
        sc.update("select_tool", "max", _out(False, 100, 0.01))        # 全失败
    assert sc.best("select_tool", ["flash", "max"]) == "flash"


def test_score_balances_latency():
    sc = ModelScorer()
    for _ in range(10):
        sc.update("select_tool", "fast", _out(True, 50, 0.001))
        sc.update("select_tool", "slow", _out(True, 500, 0.001))
    assert sc.best("select_tool", ["fast", "slow"]) == "fast"


def test_window_rolls_oldest_out():
    sc = ModelScorer(window=5)
    for i in range(8):
        sc.update("hypothesize", "m", _out(True if i < 5 else False, 100, 0.001))
    # 窗口 5:保留最新 5 条(i=3..7)= 2 成功 + 3 失败;最早的 3 条成功(i=0..2)被滚出
    stats = sc._windows[("hypothesize", "m")]
    assert len(stats) == 5                                # 窗口封顶
    assert sum(1 for s in stats if s["success"]) == 2     # 只剩 i=3,4 的成功


def test_cold_start_returns_none():
    sc = ModelScorer()
    sc.update("select_tool", "flash", _out(True, 100, 0.001))   # 仅 1 次 < MIN_SAMPLES
    assert sc.best("select_tool", ["flash", "max"]) is None     # 冷启动不瞎猜


def test_best_unknown_node_none():
    sc = ModelScorer()
    assert sc.best("unknown_node", ["a"]) is None
