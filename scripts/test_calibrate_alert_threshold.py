"""校准脚本纯函数单测(分布统计/断言/建议阈值/报告渲染;live 采集不在单测范围)。"""
import calibrate_alert_threshold as cal


def test_percentile_and_summarize():
    vals = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    s = cal.summarize(vals)
    assert s["count"] == 10
    assert s["min"] == 10 and s["max"] == 100
    assert s["p50"] == 50 and s["p90"] == 90         # 最近邻(round(idx))
    assert cal.summarize([])["count"] == 0
    assert cal.summarize([])["max"] is None


def test_parse_p95_series():
    body = {"status": "success", "data": {"result": [{"values": [
        [1, "0.012"], [16, "0.02"], [31, "0.35"],
    ]}]}}
    samples = cal.parse_p95_series(body, min_samples=3)
    assert samples == [12.0, 20.0, 350.0]            # 秒 → 毫秒

    import pytest
    with pytest.raises(ValueError):
        cal.parse_p95_series({"status": "error"}, 3)
    with pytest.raises(ValueError):
        cal.parse_p95_series({"status": "success", "data": {"result": []}}, 3)
    with pytest.raises(ValueError):
        cal.parse_p95_series({"status": "success", "data": {"result": [
            {"values": [[1, "0.01"]]}]}}, 3)          # 样本不足


def test_evaluate_pass_when_healthy_below_and_faults_above():
    phases = {
        "healthy": {"count": 60, "min": 8, "p50": 10, "p90": 12, "max": 14},
        "scn001": {"count": 60, "min": 150, "p50": 200, "p90": 400, "max": 500},
        "scn002": {"count": 60, "min": 120, "p50": 180, "p90": 350, "max": 480},
    }
    ev = cal.evaluate_calibration(phases, threshold_ms=100, min_samples=30)
    assert ev["verdict"] == "PASS"
    assert ev["healthy_ok"] is True
    assert ev["faults"]["scn001"]["ok"] and ev["faults"]["scn002"]["ok"]
    # 建议阈值:健康上界 ×1.5 → 21 → 取整 30;必须低于故障下界 120
    assert ev["recommended_ms"] == 30


def test_evaluate_fails_when_healthy_overlaps_threshold():
    phases = {
        "healthy": {"count": 60, "min": 5, "p50": 8, "p90": 95, "max": 130},
        "scn001": {"count": 60, "min": 150, "p50": 200, "p90": 400, "max": 500},
        "scn002": {"count": 60, "min": 140, "p50": 180, "p90": 350, "max": 480},
    }
    ev = cal.evaluate_calibration(phases, threshold_ms=100, min_samples=30)
    assert ev["verdict"] == "FAIL"
    assert ev["healthy_ok"] is False                  # 健康上界 130 ≥ 100


def test_evaluate_fails_when_fault_below_threshold():
    phases = {
        "healthy": {"count": 60, "min": 8, "p50": 10, "p90": 12, "max": 14},
        "scn001": {"count": 60, "min": 40, "p50": 60, "p90": 90, "max": 95},
        "scn002": {"count": 60, "min": 140, "p50": 180, "p90": 350, "max": 480},
    }
    ev = cal.evaluate_calibration(phases, threshold_ms=100, min_samples=30)
    assert ev["verdict"] == "FAIL"
    assert ev["faults"]["scn001"]["ok"] is False      # 故障下界 40 < 100:配置阈值分不开
    # 健康与故障本身可分(14 vs 40)→ 存在有效建议阈值 30;校准的意义正是发现配置错
    assert ev["recommended_ms"] == 30


def test_evaluate_insufficient_samples_is_fail():
    phases = {
        "healthy": {"count": 60, "min": 8, "p50": 10, "p90": 12, "max": 14},
        "scn001": {"count": 5, "min": 150, "p50": 200, "p90": 400, "max": 500,
                   "insufficient": True},
    }
    ev = cal.evaluate_calibration(phases, threshold_ms=100, min_samples=30)
    assert ev["verdict"] == "FAIL"
    assert ev["faults"]["scn001"]["insufficient"] is True
    assert ev["faults"]["scn001"]["ok"] is False


def test_recommend_threshold_none_when_distributions_overlap():
    assert cal.recommend_threshold(100, 100) is None
    assert cal.recommend_threshold(120, 90) is None


def test_render_markdown_contains_report_sections():
    report = {
        "generated_at": "2026-09-28T12:00:00+00:00", "prometheus": "http://p:9090",
        "threshold_ms": 100, "min_samples": 30,
        "phases": {"healthy": {"count": 60, "min": 8, "p50": 10, "p90": 12, "max": 14}},
        "evaluation": {"verdict": "FAIL", "healthy_ok": True, "threshold_ms": 100,
                       "recommended_ms": 30, "min_samples": 30,
                       "faults": {"scn001": {"min": 40, "ok": False,
                                             "insufficient": False}}},
    }
    md = cal.render_markdown(report)
    assert "校准报告" in md and "healthy" in md and "FAIL" in md
    assert "TRACEMIND_BASELINE_MAX_P95_MS=30" in md
    assert "TRACEMIND_SLO_P95_MS=30" in md
