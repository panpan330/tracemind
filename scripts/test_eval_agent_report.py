import eval_agent_report as rep


def test_aggregate_stats():
    rounds = [
        {"scenario": "SCN-001", "status": "recovered", "elapsed": 30.0,
         "observation": {"timeline": [{"type": "llm", "durationMs": 1000, "detail": {"inputTokens": 100, "outputTokens": 20}},
                                      {"type": "tool", "durationMs": 200, "detail": {"name": "get_trace"}}],
                         "diagnosis": {"anomalies": []}}},
        {"scenario": "SCN-001", "status": "recovered", "elapsed": 28.0,
         "observation": {"timeline": [{"type": "llm", "durationMs": 900, "detail": {"inputTokens": 90, "outputTokens": 18}}],
                         "diagnosis": {"anomalies": [{"type": "retry", "stepId": None, "detail": "x"}]}}},
    ]
    s = rep.aggregate(rounds)
    assert s["success_rate"] == 1.0
    assert s["avg_elapsed"] == 29.0
    assert s["avg_input_tokens"] == 95
    assert s["avg_tool_calls"] == 0.5
    assert s["anomaly_counts"]["retry"] == 1


def test_render_markdown():
    md = rep.render_markdown("20260814-120000", [
        {"scenario": "SCN-001", "round": 1, "status": "recovered", "elapsed": 30.0,
         "observation": {"timeline": [], "diagnosis": {"anomalies": []}}}],
        {"success_rate": 1.0, "avg_elapsed": 30.0, "avg_input_tokens": 100,
         "avg_output_tokens": 20, "avg_tool_calls": 1.0, "anomaly_counts": {}})
    assert "SCN-001" in md
    assert "success_rate" not in md  # 渲染成人话,非 key
    assert "recovered" in md
