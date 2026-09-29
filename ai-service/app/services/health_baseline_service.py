"""健康基线服务(V2.1-C 重写)。

- `capture_health_baseline_window`:从 Prometheus **历史窗口** [startsAt-10m,
  startsAt-1m] 采集 p95/qps/error_rate 并做最小样本数/健康阈值校验 —— 告警场景下
  "创建时的实时值"是故障态,不得充当健康基线(方案 §V2.1 条目 10)。
  实际调用方为 services/baseline_capture(阶段 2,事务外);本模块保留纯函数语义
  便于单测。窗口数据不可用 → 返回 None(调用方按 BASELINE_INSUFFICIENT 处理)。
- `capture_current_health_snapshot`:手动路径的**当前快照**(非健康基线),
  写 incident.current_health_snapshot_json,仅供坐席/调试查看。
"""
import logging
from datetime import datetime

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def capture_health_baseline_window(service_ref: str, alert_starts_at: datetime,
                                   client) -> dict | None:
    """历史窗口健康指标(仅当样本数与健康阈值均满足时返回,否则 None)。

    client 需提供 query_at(template_id, labels, window_seconds, at_time) 与
    sample_count(template_id, labels, start, end)(PrometheusMetricsClient)。
    """
    from datetime import timedelta

    from app.services.operation_registry import uri_regex_for_service

    window_start = alert_starts_at - timedelta(
        seconds=settings.baseline_window_before_start_s)
    window_end = alert_starts_at - timedelta(
        seconds=settings.baseline_window_end_offset_s)
    window_seconds = (settings.baseline_window_before_start_s
                      - settings.baseline_window_end_offset_s)
    labels = {"service": service_ref, "uri": uri_regex_for_service(service_ref),
              "extra": "", "window": f"{window_seconds}s"}
    from datetime import timezone as _tz
    start_ts, end_ts = (window_start.replace(tzinfo=_tz.utc).timestamp(),
                        window_end.replace(tzinfo=_tz.utc).timestamp())
    samples = client.sample_count("HTTP_SERVER_REQ_COUNT_V1", labels,
                                  start_ts, end_ts)
    if samples < settings.baseline_min_requests:
        logger.info("健康窗口样本 %d < %d → BASELINE_INSUFFICIENT",
                    samples, settings.baseline_min_requests)
        return None
    p95_rows = client.query_at("HTTP_SERVER_P95_V1", labels, window_seconds, end_ts)
    p95_ms = float(p95_rows[0].get("value", [0, "0"])[1]) * 1000.0
    if p95_ms > settings.baseline_max_p95_ms:
        logger.info("健康窗口 p95=%.1f 超阈值 %d → BASELINE_INSUFFICIENT(故障早于告警)",
                    p95_ms, settings.baseline_max_p95_ms)
        return None
    try:
        qps_rows = client.query_at("HTTP_SERVER_QPS_V1", labels, window_seconds, end_ts)
        qps = float(qps_rows[0].get("value", [0, "0"])[1])
    except Exception:  # noqa: BLE001 qps 缺失不阻断基线
        qps = 0.0
    try:
        err_rows = client.query_at("HTTP_SERVER_ERROR_RATE_V1", labels,
                                   window_seconds, end_ts)
        error_rate = float(err_rows[0].get("value", [0, "0"])[1])
    except Exception:  # noqa: BLE001 无 5xx 样本为合法空集
        error_rate = 0.0
    return {"p95_ms": p95_ms, "qps": qps, "error_rate": error_rate,
            "sample_count": samples}


def capture_current_health_snapshot(service_ref: str) -> dict | None:
    """调用 Java 内部观测端点取**当前快照**(非健康基线)。
    Java 未启动/异常/P95 缺失时返回 None(调用方容错)。"""
    url = f"{settings.inventory_service_url}/internal/observations/metrics?window_seconds=300"
    try:
        resp = httpx.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001 观测端点不可用不阻断 Incident 创建
        return None
    p95 = data.get("p95_ms")
    if p95 is None:  # Java 端点返回驼峰 p95Ms
        p95 = data.get("p95Ms")
    if p95 is None:
        return None
    return {"p95_ms": int(p95), "qps": data.get("qps"), "error_rate": data.get("error_rate"),
            "captured_at_is_current": True}
