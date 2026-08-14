def test_observation_endpoint_ok(monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    import app.services.observation_service as obs

    monkeypatch.setattr(obs, "build_run_observation",
                        lambda i, r: {"run": {"runId": r}, "timeline": [], "diagnosis": {}})
    monkeypatch.setattr("app.repositories.incident_repo.get_incident",
                        lambda i: type("I", (), {"id": i})())
    c = TestClient(main.app)
    resp = c.get("/api/incidents/1/runs/1/observation")
    assert resp.status_code == 200
    assert resp.json()["run"]["runId"] == 1


def test_observation_endpoint_404(monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    monkeypatch.setattr("app.repositories.incident_repo.get_incident", lambda i: None)
    c = TestClient(main.app)
    assert c.get("/api/incidents/999/runs/1/observation").status_code == 404
