"""Task 3.5: SSE 事件流(快照 + 已有事件 + Last-Event-ID 补发 + heartbeat)。

TestClient 对 text/event-stream 流式响应存在兼容问题,核心断言直接测
_event_stream 生成器;HTTP 层只验证 404 与路由注册。
"""
import pytest
from fastapi.testclient import TestClient

import app.api.stream as stream_mod
from app.main import app
from app.repositories import event_repo

client = TestClient(app)


def _create_incident_with_event():
    inc = client.post("/api/incidents", json={
        "title": "sse 测试", "severity": "medium", "service_ref": "inventory-service",
    }).json()
    event_repo.append_event(inc["id"], "status_changed", {"status": "investigating"})
    return inc


@pytest.mark.asyncio
async def test_event_stream_snapshot_then_existing_events():
    inc = _create_incident_with_event()
    gen = stream_mod._event_stream(inc["id"], 0)
    first = await anext(gen)
    assert "event: snapshot" in first
    assert f'"incident_id": {inc["id"]}' in first
    second = await anext(gen)
    assert "event: status_changed" in second
    assert "id: 1" in second
    await gen.aclose()


@pytest.mark.asyncio
async def test_event_stream_heartbeat_when_no_new_events(monkeypatch):
    monkeypatch.setattr(stream_mod, "POLL_SECONDS", 0.01)
    inc = _create_incident_with_event()
    gen = stream_mod._event_stream(inc["id"], 999)  # after 超过全部事件
    await anext(gen)  # snapshot
    line = await anext(gen)  # 轮询为空 → heartbeat
    assert line == ": ping\n\n"
    await gen.aclose()


@pytest.mark.asyncio
async def test_event_stream_respects_after_sequence():
    inc = _create_incident_with_event()
    event_repo.append_event(inc["id"], "status_changed", {"status": "awaiting_approval"})
    gen = stream_mod._event_stream(inc["id"], 1)  # 已消费 seq<=1
    await anext(gen)  # snapshot
    line = await anext(gen)
    assert "id: 2" in line
    assert "id: 1" not in line
    await gen.aclose()


def test_stream_incident_not_found():
    resp = client.get("/api/incidents/999999/stream")
    assert resp.status_code == 404


def test_stream_route_registered():
    # 路由已注册:GET 之外的 method 返回 405(而非 404 Not Found)
    resp = client.post("/api/incidents/1/stream")
    assert resp.status_code == 405
