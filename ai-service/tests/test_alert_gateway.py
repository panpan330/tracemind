"""V2.1-A:Alertmanager 接入层(网关)——特征测试先行。

范围(升级方案 §V2.1-A,不越界):
- Bearer Token 鉴权、Pydantic 严格 Schema、载荷边界(大小/alerts 数量/alertname/标签);
- AlertEvent 原始事件落库 + delivery_hash 重放幂等;
- alert_instance_key 与 FIRING → RESOLVED 单向推进(迟到 FIRING 只归档);
- resolved-only 且无既有 Incident 时只归档;
- 不创建 Incident / 不启动 Run(V2.1-B 范围,响应中 created/updated 恒为空)。
"""
import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.engine import get_control_engine
from app.incident_gateway import service as gateway_service
from app.incident_gateway.fingerprint import (alert_instance_key, delivery_hash,
                                              normalize_labels)
from app.incident_gateway.schemas import AlertmanagerWebhookIn


# ---------- fixtures / helpers ----------

def _firing_payload(*, fingerprint=None, starts_at="2026-09-04T05:00:00.000Z",
                    status="firing", labels=None, ends_at=None) -> dict:
    return {
        "version": "4", "groupKey": "g1", "status": status, "receiver": "tracemind",
        "alerts": [{
            "status": status,
            "labels": labels if labels is not None else {
                "alertname": "OrderOperationP95High", "service": "order-service",
                "operation": "ORDER_CREATE", "environment": "demo"},
            "annotations": {"summary": "P95 高"},
            "startsAt": starts_at, "endsAt": ends_at,
            "generatorURL": "http://prom", "fingerprint": fingerprint or uuid.uuid4().hex[:12],
        }],
    }


def _cleanup(keys=None, fingerprints=None):
    with Session(get_control_engine()) as s:
        if fingerprints:
            s.execute(text("DELETE FROM alert_instance WHERE external_fingerprint IN :f"),
                      {"f": tuple(fingerprints)})
            s.execute(text("DELETE FROM alert_event WHERE external_fingerprint IN :f"),
                      {"f": tuple(fingerprints)})
        s.commit()


# ---------- Schema 严格解析 ----------

def test_schema_strict_label_values_must_be_strings():
    payload = _firing_payload()
    payload["alerts"][0]["labels"]["service"] = 123  # 非 str
    with pytest.raises(ValueError):
        AlertmanagerWebhookIn.model_validate(payload)


def test_schema_requires_fingerprint_and_starts_at():
    payload = _firing_payload()
    del payload["alerts"][0]["fingerprint"]
    with pytest.raises(ValueError):
        AlertmanagerWebhookIn.model_validate(payload)
    payload2 = _firing_payload()
    del payload2["alerts"][0]["startsAt"]
    with pytest.raises(ValueError):
        AlertmanagerWebhookIn.model_validate(payload2)


def test_schema_parses_rfc3339_and_allows_unknown_top_level():
    payload = _firing_payload()
    payload["truncatedAlerts"] = 0          # AM 顶层扩展字段允许(ignore)
    payload["alerts"][0]["startsAt"] = "2026-09-04T05:00:00.123Z"
    model = AlertmanagerWebhookIn.model_validate(payload)
    assert model.alerts[0].startsAt.year == 2026


# ---------- delivery_hash / alert_instance_key ----------

def test_delivery_hash_is_canonical_order_insensitive():
    a = {"status": "firing", "labels": {"alertname": "X", "service": "y"},
         "startsAt": "t1", "fingerprint": "fp1"}
    b = {"labels": {"service": "y", "alertname": "X"}, "fingerprint": "fp1",
         "startsAt": "t1", "status": "firing"}
    assert delivery_hash(a) == delivery_hash(b)          # 规范化排序,键序不敏感
    c = dict(a, status="resolved")
    assert delivery_hash(c) != delivery_hash(a)


def test_alert_instance_key_distinguishes_starts_at():
    k1 = alert_instance_key("alertmanager", "fp1", "2026-09-04T05:00:00Z")
    k2 = alert_instance_key("alertmanager", "fp1", "2026-09-04T05:01:00Z")
    assert k1 != k2                                      # 更晚 startsAt = 新实例
    assert alert_instance_key("alertmanager", "fp1", "t") == \
        alert_instance_key("alertmanager", "fp1", "t")


# ---------- 标签边界 ----------

def test_normalize_labels_filters_and_detects_overlong():
    labels = {"alertname": "OrderOperationP95High", "service": "order-service",
              "job": "tracemind-java", "secret_label": "strip-me",
              "operation": "O" * 200}
    kept, violations = normalize_labels(labels, max_value_len=128)
    assert "secret_label" not in kept and kept["service"] == "order-service"
    assert any("operation" in v for v in violations)     # 超长值 → violation → ignored


# ---------- 服务层:落库/幂等/单向推进 ----------

def _process(payload):
    model = AlertmanagerWebhookIn.model_validate(payload)
    return gateway_service.process_alert_batch("alertmanager", model)


def test_firing_creates_event_and_firing_instance():
    fp = uuid.uuid4().hex[:12]
    out = _process(_firing_payload(fingerprint=fp))
    assert out["received"] == 1 and out["created_incidents"] == []
    assert out["events"]["appended"] == 1 and out["events"]["duplicates"] == 0
    with Session(get_control_engine()) as s:
        inst = s.execute(text(
            "SELECT current_status, version FROM alert_instance WHERE external_fingerprint=:f"),
            {"f": fp}).fetchone()
        assert inst.current_status == "FIRING" and inst.version == 1
        ev = s.execute(text(
            "SELECT COUNT(*) FROM alert_event WHERE external_fingerprint=:f"),
            {"f": fp}).scalar()
        assert ev == 1
    _cleanup(fingerprints=[fp])


def test_exact_replay_is_idempotent_ten_times():
    fp = uuid.uuid4().hex[:12]
    payload = _firing_payload(fingerprint=fp)
    for _ in range(10):
        out = _process(payload)
    with Session(get_control_engine()) as s:
        ev = s.execute(text("SELECT COUNT(*) FROM alert_event WHERE external_fingerprint=:f"),
                       {"f": fp}).scalar()
        inst = s.execute(text("SELECT version FROM alert_instance WHERE external_fingerprint=:f"),
                         {"f": fp}).scalar()
    assert ev == 1 and inst == 1        # 10 次重放:1 事件,version 不推进
    assert out["events"]["duplicates"] == 1  # 第 10 次计 duplicate
    _cleanup(fingerprints=[fp])


def test_resolved_advances_firing_to_resolved_one_way():
    fp = uuid.uuid4().hex[:12]
    _process(_firing_payload(fingerprint=fp))
    out = _process(_firing_payload(fingerprint=fp, status="resolved",
                                   ends_at="2026-09-04T05:05:00.000Z"))
    with Session(get_control_engine()) as s:
        inst = s.execute(text(
            "SELECT current_status, version, resolved_at FROM alert_instance "
            "WHERE external_fingerprint=:f"), {"f": fp}).fetchone()
    assert inst.current_status == "RESOLVED" and inst.version == 2
    assert inst.resolved_at is not None
    # 迟到旧 FIRING(负载不同:多出 annotation,delivery_hash 不同):只归档,不重新打开
    late = _firing_payload(fingerprint=fp)
    late["alerts"][0]["annotations"] = {"summary": "late-renotify"}
    out2 = _process(late)
    with Session(get_control_engine()) as s:
        inst2 = s.execute(text(
            "SELECT current_status, version FROM alert_instance WHERE external_fingerprint=:f"),
            {"f": fp}).fetchone()
        ev = s.execute(text("SELECT COUNT(*) FROM alert_event WHERE external_fingerprint=:f "
                            "AND alert_status='firing'"), {"f": fp}).scalar()
    assert inst2.current_status == "RESOLVED" and inst2.version == 2
    assert ev == 2                      # 迟到 FIRING 事件已归档
    assert out2["events"]["archived_late_firing"] == 1
    _cleanup(fingerprints=[fp])


def test_resolved_only_without_instance_is_archived():
    """resolved-only 且无既有实例:完整记录(事件 + RESOLVED 投影),不触发下游。"""
    fp = uuid.uuid4().hex[:12]
    out = _process(_firing_payload(fingerprint=fp, status="resolved",
                                   ends_at="2026-09-04T05:05:00.000Z"))
    with Session(get_control_engine()) as s:
        inst = s.execute(text(
            "SELECT current_status FROM alert_instance WHERE external_fingerprint=:f"),
            {"f": fp}).fetchone()
    assert inst.current_status == "RESOLVED"
    assert out["created_incidents"] == []   # A 阶段不创建 Incident(V2.1-B)
    _cleanup(fingerprints=[fp])


def test_unknown_alertname_and_overlong_label_are_ignored():
    fp_known = uuid.uuid4().hex[:12]
    payload = _firing_payload(fingerprint=fp_known)
    payload["alerts"].append({
        "status": "firing",
        "labels": {"alertname": "UnknownAlert", "service": "x"},
        "startsAt": "2026-09-04T05:00:00.000Z", "fingerprint": uuid.uuid4().hex[:12],
    })
    payload["alerts"].append({
        "status": "firing",
        "labels": {"alertname": "OrderOperationP95High",
                   "service": "o" * 300},   # 超长
        "startsAt": "2026-09-04T05:00:00.000Z", "fingerprint": uuid.uuid4().hex[:12],
    })
    out = _process(payload)
    assert out["received"] == 3 and out["ignored"] == 2
    with Session(get_control_engine()) as s:
        n = s.execute(text("SELECT COUNT(*) FROM alert_event WHERE "
                           "external_fingerprint IN (:a, :b)"),
                      {"a": payload["alerts"][1]["fingerprint"],
                       "b": payload["alerts"][2]["fingerprint"]}).scalar()
    assert n == 0                        # 被忽略的告警不落库
    _cleanup(fingerprints=[fp_known])


# ---------- HTTP 端点(鉴权/边界) ----------

@pytest.fixture()
def api(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    monkeypatch.setattr(settings, "alertmanager_webhook_token", "test-token-123")
    return TestClient(app)


def test_webhook_requires_bearer_token(api):
    payload = _firing_payload(fingerprint=uuid.uuid4().hex[:12])
    r1 = api.post("/api/integrations/alertmanager/webhook", json=payload)
    assert r1.status_code == 401
    r2 = api.post("/api/integrations/alertmanager/webhook", json=payload,
                  headers={"Authorization": "Bearer wrong"})
    assert r2.status_code == 401


def test_webhook_disabled_without_token(api, monkeypatch):
    monkeypatch.setattr(settings, "alertmanager_webhook_token", "")
    r = api.post("/api/integrations/alertmanager/webhook",
                 json=_firing_payload(fingerprint=uuid.uuid4().hex[:12]),
                 headers={"Authorization": "Bearer anything"})
    assert r.status_code == 403


def test_webhook_rejects_oversized_body(api, monkeypatch):
    monkeypatch.setattr(settings, "alertmanager_max_body_bytes", 256)
    big = _firing_payload(fingerprint=uuid.uuid4().hex[:12])
    big["alerts"][0]["annotations"] = {"summary": "x" * 4096}
    r = api.post("/api/integrations/alertmanager/webhook", json=big,
                 headers={"Authorization": "Bearer test-token-123"})
    assert r.status_code == 413


def test_webhook_rejects_too_many_alerts(api, monkeypatch):
    monkeypatch.setattr(settings, "alertmanager_max_alerts", 2)
    payload = _firing_payload(fingerprint=uuid.uuid4().hex[:12])
    for _ in range(3):
        payload["alerts"].append(dict(payload["alerts"][0],
                                      fingerprint=uuid.uuid4().hex[:12]))
    r = api.post("/api/integrations/alertmanager/webhook", json=payload,
                 headers={"Authorization": "Bearer test-token-123"})
    assert r.status_code == 422


def test_webhook_rejects_invalid_schema(api):
    payload = _firing_payload(fingerprint=uuid.uuid4().hex[:12])
    payload["alerts"][0]["labels"] = {"alertname": 42}
    r = api.post("/api/integrations/alertmanager/webhook", json=payload,
                 headers={"Authorization": "Bearer test-token-123"})
    assert r.status_code == 422


def test_webhook_accepts_valid_batch(api):
    fp = uuid.uuid4().hex[:12]
    r = api.post("/api/integrations/alertmanager/webhook",
                 json=_firing_payload(fingerprint=fp),
                 headers={"Authorization": "Bearer test-token-123"})
    assert r.status_code == 200
    body = r.json()
    assert body["received"] == 1
    assert body["created_incidents"] == [] and body["updated_incidents"] == []
    _cleanup(fingerprints=[fp])
