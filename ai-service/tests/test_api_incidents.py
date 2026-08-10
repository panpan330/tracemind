from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_create_incident():
    resp = client.post("/api/incidents", json={
        "title": "库存查询变慢", "description": "P95 升高", "severity": "high",
        "service_ref": "inventory-service",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] is not None
    assert body["status"] == "created"


def test_start_investigation_returns_202():
    inc = client.post("/api/incidents", json={
        "title": "t", "severity": "medium", "service_ref": "inventory-service"}).json()
    resp = client.post(f"/api/incidents/{inc['id']}/investigations")
    assert resp.status_code == 202
    assert resp.json()["run_id"] is not None


def test_tool_entry_point():
    inc = client.post("/api/incidents", json={
        "title": "t", "severity": "medium", "service_ref": "inventory-service"}).json()
    resp = client.post(f"/api/incidents/{inc['id']}/tools", json={
        "tool": "get_index_info", "args": {"table_ref": "inventory"}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "PRIMARY" in [i["index_name"] for i in body["data"]["indexes"]]
