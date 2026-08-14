from app.agent.evidence_summary import summarize, _key_metric


def test_summarize_within_threshold_unchanged():
    ev = [{"id": "E1", "passed": True, "content": {"p95Ms": 500}}]
    assert summarize(ev) == ev


def test_summarize_over_threshold_compresses_old():
    ev = [{"id": f"E{i}", "passed": True, "key": "E1",
           "content": {"p95Ms": 100 * i}} for i in range(1, 11)]  # 10 条
    out = summarize(ev, max_keep=8)
    assert len(out) == 10            # 条数不变,仅压缩 content
    assert isinstance(out[0]["content"], str)   # 最旧 2 条被摘要
    assert isinstance(out[-1]["content"], dict)  # 最近 8 条保留完整


def test_key_metric_metrics():
    assert "500" in _key_metric({"key": "E1", "content": {"p95Ms": 500}})


def test_key_metric_lock():
    assert "3000" in _key_metric({"key": "L1", "content": {"wait_duration_ms": 3000}})


def test_key_metric_fallback():
    assert _key_metric({"key": "X", "content": {"other": 1}}) == "passed=None"
