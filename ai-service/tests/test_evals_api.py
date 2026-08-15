"""V1.13 评测 API 测试(monkeypatch repo,不连真实库)。"""
from fastapi.testclient import TestClient

import app.main as main
from app.repositories import eval_run_repo

client = TestClient(main.app)


def test_evals_list_empty_ok(monkeypatch):
    monkeypatch.setattr(eval_run_repo, "list_eval_runs", lambda: [])
    r = client.get("/api/evals")
    assert r.status_code == 200
    assert r.json() == []


def test_evals_list_returns_rows(monkeypatch):
    monkeypatch.setattr(eval_run_repo, "list_eval_runs", lambda: [
        {"id": 1, "created_at": "2026-08-14 16:00:00", "scenario": "SCN-001",
         "rounds": 3, "success_rate": 0.667, "avg_duration_ms": 45000,
         "total_cost": 0.02, "model_snapshot": "qwen3.8-max"}])
    r = client.get("/api/evals")
    assert r.status_code == 200
    assert r.json()[0]["scenario"] == "SCN-001"


def test_evals_detail_ok(monkeypatch):
    monkeypatch.setattr(eval_run_repo, "get_eval_run", lambda i: {
        "id": i, "scenario": "SCN-002", "rounds": 1, "success_rate": 1.0,
        "avg_duration_ms": 30000, "total_cost": 0.01, "model_snapshot": "qwen3.7-flash",
        "summary": "recovered", "raw_json": '{"rounds":[{"round":1}]}'})
    r = client.get("/api/evals/7")
    assert r.status_code == 200
    assert r.json()["id"] == 7


def test_evals_detail_missing_404(monkeypatch):
    monkeypatch.setattr(eval_run_repo, "get_eval_run", lambda i: None)
    r = client.get("/api/evals/999999")
    assert r.status_code == 404


def test_evals_run_validates_params():
    r = client.post("/api/evals/run", json={"scenario": "BAD_SCENARIO", "rounds": 9})
    assert r.status_code == 400
    r2 = client.post("/api/evals/run", json={"scenario": "SCN-001", "rounds": "x"})
    assert r2.status_code == 400


def test_evals_run_accepts(monkeypatch):
    started = []

    def fake_run(scenario, rounds):
        started.append((scenario, rounds))
    monkeypatch.setattr("app.api.evals._run_eval_background", fake_run)
    r = client.post("/api/evals/run", json={"scenario": "SCN-001", "rounds": 1})
    assert r.status_code == 202
    assert started == [("SCN-001", 1)]
