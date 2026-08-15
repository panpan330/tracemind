"""V1.13 评测报告写库增强单测。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 项目根,供 import scripts

from scripts import eval_agent_report as mod


def _rounds():
    return [{"round": 1, "scenario": "SCN-001", "status": "recovered",
             "elapsed": 45.0,
             "observation": {"timeline": [], "diagnosis": {"anomalies": []}}}]


def test_write_report_writes_md_and_db(monkeypatch, tmp_path):
    from app.repositories import eval_run_repo
    inserted = []
    monkeypatch.setattr(eval_run_repo, "insert_eval_run",
                        lambda **kw: inserted.append(kw) or 1)
    rounds = _rounds()
    stats = mod.aggregate(rounds)
    ts = "20260814-160000"
    out = mod.write_report(ts, rounds, stats, Path(tmp_path))
    # md 文件生成
    assert out.exists()
    assert "SCN-001" in out.read_text(encoding="utf-8")
    # 写库一次
    assert len(inserted) == 1
    assert inserted[0]["scenario"] == "SCN-001"
    assert inserted[0]["success_rate"] == 1.0
    assert inserted[0]["summary"] == "1/1 recovered"
    # raw_json 是 rounds 的 JSON
    parsed = json.loads(inserted[0]["raw_json"])
    assert parsed[0]["round"] == 1


def test_write_report_db_failure_degrades(monkeypatch, tmp_path):
    """写库失败 → 只出 md,不抛异常。"""
    from app.repositories import eval_run_repo

    def _boom(**kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(eval_run_repo, "insert_eval_run", _boom)
    rounds = _rounds()
    stats = mod.aggregate(rounds)
    out = mod.write_report("20260814-160001", rounds, stats, Path(tmp_path))
    assert out.exists()
