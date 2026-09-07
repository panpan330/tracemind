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


@pytest.fixture(autouse=True)
def _clean_demo_group():
    """共享 demo group 从零开始(共享测试库;否则前一测试的 OPEN Incident 被并入,
    created_incidents 断言不确定)。"""
    from tests.test_alert_aggregation import _purge_demo_group
    _purge_demo_group()
    yield
    _purge_demo_group()

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
    """清理告警与 B 阶段聚合产物(Run/关联/Incident;顺序:子→父)。"""
    with Session(get_control_engine()) as s:
        if fingerprints:
            fps = tuple(fingerprints)
            s.execute(text(
                "DELETE agent_run FROM agent_run JOIN incident ON "
                "agent_run.incident_id = incident.id WHERE incident.id IN "
                "(SELECT incident_id FROM incident_alert ia JOIN alert_instance ai ON "
                "ai.alert_instance_key = ia.alert_instance_key "
                "WHERE ai.external_fingerprint IN :f)"), {"f": fps})
            s.execute(text(
                "DELETE incident FROM incident WHERE incident.id IN "
                "(SELECT incident_id FROM incident_alert ia JOIN alert_instance ai ON "
                "ai.alert_instance_key = ia.alert_instance_key "
                "WHERE ai.external_fingerprint IN :f)"), {"f": fps})
            s.execute(text(
                "DELETE FROM incident_alert WHERE alert_instance_key IN "
                "(SELECT alert_instance_key FROM alert_instance "
                "WHERE external_fingerprint IN :f)"), {"f": fps})
            s.execute(text("DELETE FROM alert_instance WHERE external_fingerprint IN :f"),
                      {"f": fps})
            s.execute(text("DELETE FROM alert_event WHERE external_fingerprint IN :f"),
                      {"f": fps})
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
    # V2.1-B:FIRING 聚合创建 Incident(created_incidents 恒空是 V2.1-A 语义)
    assert out["received"] == 1 and len(out["created_incidents"]) == 1
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
    # V2.1-B:FIRING 聚合创建 Incident(不再恒空;Agent 由 Dispatcher 启动,不在此处)
    assert len(body["created_incidents"]) == 1
    _cleanup(fingerprints=[fp])


# ---------- V2.1-A closure:乱序 FIRING / 时区归一 / 并发一致性 ----------

def _fp_payload(fp, starts_at, status="firing", **extra):
    p = _firing_payload(fingerprint=fp, starts_at=starts_at, status=status, **extra)
    return p


def test_late_unseen_earlier_firing_archived_no_instance():
    """10:00 FIRING 在先;未见过且更早的 09:00 FIRING → 只归档事件,不建实例。"""
    fp = uuid.uuid4().hex[:12]
    _process(_fp_payload(fp, "2026-09-04T10:00:00.000Z"))
    out = _process(_fp_payload(fp, "2026-09-04T09:00:00.000Z"))
    with Session(get_control_engine()) as s:
        n_inst = s.execute(text("SELECT COUNT(*) FROM alert_instance "
                                "WHERE external_fingerprint=:f"), {"f": fp}).scalar()
        statuses = [r[0] for r in s.execute(text(
            "SELECT current_status FROM alert_instance WHERE external_fingerprint=:f"),
            {"f": fp}).fetchall()]
        ev = s.execute(text("SELECT COUNT(*) FROM alert_event "
                            "WHERE external_fingerprint=:f"), {"f": fp}).scalar()
    assert n_inst == 1 and statuses == ["FIRING"]   # 只保留一个 FIRING 实例
    assert ev == 2                                   # 09:00 事件已归档
    assert out["events"]["archived_late_firing"] == 1
    _cleanup(fingerprints=[fp])


def test_later_starts_at_creates_new_instance():
    """10:00 FIRING 后到达 11:00 FIRING(更晚)→ 可创建新实例。"""
    fp = uuid.uuid4().hex[:12]
    _process(_fp_payload(fp, "2026-09-04T10:00:00.000Z"))
    _process(_fp_payload(fp, "2026-09-04T11:00:00.000Z"))
    with Session(get_control_engine()) as s:
        rows = s.execute(text("SELECT starts_at, current_status FROM alert_instance "
                              "WHERE external_fingerprint=:f ORDER BY starts_at"),
                         {"f": fp}).fetchall()
    assert len(rows) == 2
    assert rows[0].current_status == "FIRING" and rows[1].current_status == "FIRING"
    _cleanup(fingerprints=[fp])


def test_same_moment_different_timezone_single_instance():
    """同一时刻的 Z 与 +02:00 表达 → 归一化后同一实例(第二个为精确重放)。"""
    fp = uuid.uuid4().hex[:12]
    _process(_fp_payload(fp, "2026-09-04T05:00:00.000Z"))
    out = _process(_fp_payload(fp, "2026-09-04T07:00:00.000+02:00"))
    with Session(get_control_engine()) as s:
        n_inst = s.execute(text("SELECT COUNT(*) FROM alert_instance "
                                "WHERE external_fingerprint=:f"), {"f": fp}).scalar()
        n_ev = s.execute(text("SELECT COUNT(*) FROM alert_event "
                              "WHERE external_fingerprint=:f"), {"f": fp}).scalar()
    assert n_inst == 1 and n_ev == 1               # 同一时刻不产生第二个实例
    assert out["events"]["duplicates"] == 1         # 规范化后 delivery_hash 相同
    _cleanup(fingerprints=[fp])


def test_concurrent_identical_deliveries_single_event_and_instance():
    """10 个并发相同告警:最终只有 1 个 AlertEvent、1 个 AlertInstance。"""
    from concurrent.futures import ThreadPoolExecutor

    fp = uuid.uuid4().hex[:12]
    payload = _firing_payload(fingerprint=fp)
    model = AlertmanagerWebhookIn.model_validate(payload)

    def _deliver(_):
        return gateway_service.process_alert_batch("alertmanager", model)

    with ThreadPoolExecutor(max_workers=10) as pool:
        outs = list(pool.map(_deliver, range(10)))
    with Session(get_control_engine()) as s:
        n_ev = s.execute(text("SELECT COUNT(*) FROM alert_event "
                              "WHERE external_fingerprint=:f"), {"f": fp}).scalar()
        n_inst = s.execute(text("SELECT COUNT(*) FROM alert_instance "
                                "WHERE external_fingerprint=:f"), {"f": fp}).scalar()
        ver = s.execute(text("SELECT version FROM alert_instance "
                             "WHERE external_fingerprint=:f"), {"f": fp}).scalar()
    assert n_ev == 1 and n_inst == 1 and ver == 1
    assert sum(o["events"]["appended"] for o in outs) == 1
    assert sum(o["events"]["duplicates"] for o in outs) == 9
    _cleanup(fingerprints=[fp])


def test_concurrent_firing_resolved_final_resolved():
    """并发 FIRING/RESOLVED 混投:最终状态只能为 RESOLVED。"""
    from concurrent.futures import ThreadPoolExecutor

    fp = uuid.uuid4().hex[:12]
    model_f = AlertmanagerWebhookIn.model_validate(
        _fp_payload(fp, "2026-09-04T05:00:00.000Z"))
    model_r = AlertmanagerWebhookIn.model_validate(
        _fp_payload(fp, "2026-09-04T05:00:00.000Z", status="resolved",
                    ends_at="2026-09-04T05:01:00.000Z"))

    def _deliver(m):
        return gateway_service.process_alert_batch("alertmanager", m)

    jobs = [model_f, model_r] * 5
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(_deliver, jobs))
    with Session(get_control_engine()) as s:
        st = s.execute(text("SELECT current_status FROM alert_instance "
                            "WHERE external_fingerprint=:f"), {"f": fp}).scalar()
        ver = s.execute(text("SELECT version FROM alert_instance "
                             "WHERE external_fingerprint=:f"), {"f": fp}).scalar()
    assert st == "RESOLVED"          # 任何交错下终态只能为 RESOLVED(单向)
    # version ∈ {1,2}:若 RESOLVED 先被处理,FIRING 全部被最晚-startsAt 规则归档(无推进)
    assert ver in (1, 2)
    _cleanup(fingerprints=[fp])


def test_concurrent_various_starts_at_latest_wins():
    """并发不同 startsAt:任何到达顺序下,实例创建序列的 starts_at 严格递增
    (last_event_id 即创建顺序);最大 startsAt 的实例必为 FIRING。"""
    from concurrent.futures import ThreadPoolExecutor

    fp = uuid.uuid4().hex[:12]
    starts = ["2026-09-04T09:00:00.000Z", "2026-09-04T11:00:00.000Z",
              "2026-09-04T10:00:00.000Z", "2026-09-04T11:30:00.000Z"]
    models = [AlertmanagerWebhookIn.model_validate(_fp_payload(fp, st)) for st in starts]

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda m: gateway_service.process_alert_batch("alertmanager", m),
                      models))
    with Session(get_control_engine()) as s:
        rows = s.execute(text(
            "SELECT starts_at, current_status, last_event_id FROM alert_instance "
            "WHERE external_fingerprint=:f ORDER BY last_event_id"), {"f": fp}).fetchall()
        evs = s.execute(text("SELECT COUNT(*) FROM alert_event "
                             "WHERE external_fingerprint=:f"), {"f": fp}).scalar()
    assert evs == 4                                  # 每个到达都归档事件
    assert rows[-1].current_status == "FIRING"       # 最后创建的 = 最晚 startsAt
    assert rows[-1].starts_at == datetime(2026, 9, 4, 11, 30)
    created = [r.starts_at for r in rows]
    assert all(a < b for a, b in zip(created, created[1:]))  # 创建序列严格递增
    _cleanup(fingerprints=[fp])


# ---------- V2.1-A closure:请求体大小限制(真实字节数,不信任 Content-Length) ----------

def test_webhook_forged_content_length_still_enforced(api):
    """伪造较小的 Content-Length:实际字节超限仍 413,且不进入解析。"""
    big = json.dumps(_firing_payload(fingerprint=uuid.uuid4().hex[:12])).encode()
    big = big + b'{"pad":"' + b"x" * (settings.alertmanager_max_body_bytes + 4096) + b'"}'
    r = api.post("/api/integrations/alertmanager/webhook", content=big,
                 headers={"Authorization": "Bearer test-token-123",
                          "Content-Type": "application/json",
                          "Content-Length": "10"})   # 伪造
    assert r.status_code == 413


def test_webhook_chunked_oversize_without_content_length(api):
    """无 Content-Length(分块传输):流式累计超限 → 413。"""
    pad = b"x" * 4096
    chunks = [b'{"junk":"' , pad, pad, pad, pad, pad, pad, pad, pad, pad, pad,
              pad, pad, pad, pad, pad, pad, b'"}']

    def stream():
        yield from chunks

    r = api.post("/api/integrations/alertmanager/webhook", content=stream(),
                 headers={"Authorization": "Bearer test-token-123",
                          "Content-Type": "application/json"})
    assert r.status_code == 413


def test_webhook_normal_payload_still_accepted_after_hardening(api):
    fp = uuid.uuid4().hex[:12]
    r = api.post("/api/integrations/alertmanager/webhook",
                 json=_firing_payload(fingerprint=fp),
                 headers={"Authorization": "Bearer test-token-123"})
    assert r.status_code == 200
    _cleanup(fingerprints=[fp])


# ---------- V2.1-A micro-closure:锁名哈希化 + 64 字符 fingerprint 契约 ----------

def test_gateway_lock_name_contract():
    """锁名:确定性、source 隔离、恒为 64 十六进制(MySQL GET_LOCK 上限)。"""
    from app.incident_gateway.fingerprint import gateway_lock_name

    n1 = gateway_lock_name("alertmanager", "f" * 64)
    assert n1 == gateway_lock_name("alertmanager", "f" * 64)      # 确定性
    assert len(n1) == 64 and all(ch in "0123456789abcdef" for ch in n1)
    assert n1 != gateway_lock_name("othersource", "f" * 64)       # source 隔离
    assert n1 != gateway_lock_name("alertmanager", "f" * 63 + "g")  # 指纹隔离
    assert gateway_lock_name("s", "x") == gateway_lock_name("s", "x")


def test_webhook_64char_fingerprint_full_flow(api):
    """Schema 允许的 64 字符 fingerprint:200 + AlertEvent + AlertInstance 完整生成
    (回归:哈希锁名前,85 字符拼接锁名使 GET_LOCK 报 4163 → 500)。"""
    fp = "f" * 56 + uuid.uuid4().hex[:8]   # 64 字符且每次唯一(不与 live 冒烟撞键)
    payload = _firing_payload(fingerprint=fp,
                              starts_at="2026-09-05T02:00:00.000Z")
    r = api.post("/api/integrations/alertmanager/webhook", json=payload,
                 headers={"Authorization": "Bearer test-token-123"})
    assert r.status_code == 200
    with Session(get_control_engine()) as s:
        ev = s.execute(text("SELECT COUNT(*) FROM alert_event "
                            "WHERE external_fingerprint=:f"), {"f": fp}).scalar()
        inst = s.execute(text("SELECT current_status FROM alert_instance "
                              "WHERE external_fingerprint=:f"), {"f": fp}).scalar()
    assert ev == 1 and inst == "FIRING"
    # RESOLVED 推进同指纹(锁名相同路径)仍正常
    r2 = api.post("/api/integrations/alertmanager/webhook",
                  json=_firing_payload(fingerprint=fp,
                                       starts_at="2026-09-05T02:00:00.000Z",
                                       status="resolved",
                                       ends_at="2026-09-05T02:01:00.000Z"),
                  headers={"Authorization": "Bearer test-token-123"})
    assert r2.status_code == 200
    with Session(get_control_engine()) as s:
        st, ver = s.execute(text("SELECT current_status, version FROM alert_instance "
                                 "WHERE external_fingerprint=:f"), {"f": fp}).fetchone()
    assert st == "RESOLVED" and ver == 2
    _cleanup(fingerprints=[fp])
