"""V2.1-C:启动前基线采集(两阶段冻结的阶段 2,事务外执行)。

职责:为 alertmanager 触发的 queued Run 采集两类基线 ——
- digest 快照:业务库 performance_schema 累计计数(只读连接,用后即关);
- 健康基线:Prometheus 历史窗口 [startsAt-10m, startsAt-1m] 的 p95/qps/error_rate,
  以最小样本数与健康阈值校验,不合格记 BASELINE_INSUFFICIENT(不伪造)。

硬性边界:本模块不做任何数据库**写**操作、不开启事务;外部 I/O(Prometheus HTTP、
业务库只读)全部发生在封存 CAS 事务之前(run_repo.seal_run_baselines_and_dispatch)。
状态语义:OK / INSUFFICIENT 为封存终值(不重采);CAPTURE_FAILED = 未封存,
可按封存 CAS 同一条件重试。
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.engine import get_control_engine
from app.services.baseline_service import capture_digest_baseline
from app.services.operation_registry import uri_regex_for_service
from app.services.prometheus_client import PrometheusMetricsClient

logger = logging.getLogger(__name__)

STATUS_OK = "OK"
STATUS_INSUFFICIENT = "INSUFFICIENT"
STATUS_CAPTURE_FAILED = "CAPTURE_FAILED"


@dataclass
class BaselineCapture:
    status: str                    # OK | INSUFFICIENT | CAPTURE_FAILED
    digest_baseline: dict | None = None
    healthy_metrics: dict | None = None    # 仅 OK 时非空(p95_ms/qps/error_rate/sample_count)
    window_start: datetime | None = None
    window_end: datetime | None = None


def _prometheus_client() -> PrometheusMetricsClient:
    return PrometheusMetricsClient()


def _alert_starts_at(incident_id: int) -> datetime | None:
    """告警 startsAt = 关联实例最早 starts_at(控制库只读,不开启写事务)。"""
    with Session(get_control_engine()) as s:
        return s.execute(text(
            "SELECT MIN(ai.starts_at) FROM incident_alert ia "
            "JOIN alert_instance ai ON ai.alert_instance_key = ia.alert_instance_key "
            "WHERE ia.incident_id = :i"), {"i": incident_id}).scalar()


def capture_run_baselines(run, *, alert_starts_at: datetime | None = None) -> BaselineCapture:
    """采集该 Run 的两类基线(阶段 2;禁止数据库写操作/事务)。

    任意一步异常 → CAPTURE_FAILED(可按封存 CAS 重试);健康窗口数据可用但
    不满足最小样本数/健康阈值 → INSUFFICIENT(封存终值,健康指标不落库)。
    """
    from app.services.run_context import RunContextInvalid, RunContextMissing, load_snapshot

    try:
        snap = load_snapshot(run)
    except (RunContextMissing, RunContextInvalid):
        logger.exception("baseline capture: run %s 快照不可用(fail closed)", run.id)
        return BaselineCapture(status=STATUS_CAPTURE_FAILED)

    # --- digest 快照(业务库只读) ---
    try:
        from app.db.engine import get_readonly_engine
        digest = capture_digest_baseline(get_readonly_engine())
        if not isinstance(digest, dict):
            raise ValueError("digest snapshot is not a dict")
    except Exception:  # noqa: BLE001 采集异常 → 未封存,可重试
        logger.exception("baseline capture: run %s digest 采集失败", run.id)
        return BaselineCapture(status=STATUS_CAPTURE_FAILED)

    # --- 健康基线:Prometheus 历史窗口 ---
    starts_at = alert_starts_at if alert_starts_at is not None \
        else _alert_starts_at(run.incident_id)
    if starts_at is None:
        # 无告警时间参照(理论上仅手动 Run;手动 Run 不进入阶段 2)→ 不伪造窗口
        logger.warning("baseline capture: run %s 无告警 startsAt,健康基线记 INSUFFICIENT",
                       run.id)
        return BaselineCapture(status=STATUS_INSUFFICIENT, digest_baseline=digest)

    starts_at = _to_naive_utc(starts_at)
    window_start = starts_at - timedelta(seconds=settings.baseline_window_before_start_s)
    window_end = starts_at - timedelta(seconds=settings.baseline_window_end_offset_s)
    window_seconds = settings.baseline_window_before_start_s \
        - settings.baseline_window_end_offset_s
    labels = {"service": snap.service_ref,
              "uri": uri_regex_for_service(snap.service_ref),
              "extra": "", "window": f"{window_seconds}s"}
    try:
        client = _prometheus_client()
        start_ts = window_start.timestamp()
        end_ts = window_end.timestamp()
        samples = client.sample_count("HTTP_SERVER_REQ_COUNT_V1", labels,
                                      start_ts, end_ts)
        if samples < settings.baseline_min_samples:
            logger.info("baseline capture: run %s 窗口样本 %d < %d → INSUFFICIENT",
                        run.id, samples, settings.baseline_min_samples)
            return BaselineCapture(status=STATUS_INSUFFICIENT, digest_baseline=digest,
                                   window_start=window_start, window_end=window_end)
        p95_rows = client.query_at("HTTP_SERVER_P95_V1", labels, window_seconds, end_ts)
        p95_ms = float(p95_rows[0].get("value", [0, "0"])[1]) * 1000.0
        if p95_ms > settings.baseline_max_p95_ms:
            # 故障在告警触发前已发生,窗口被污染 → 不得充当健康基线
            logger.info("baseline capture: run %s 窗口 p95=%.1f 超健康阈值 %d "
                        "→ INSUFFICIENT", run.id, p95_ms, settings.baseline_max_p95_ms)
            return BaselineCapture(status=STATUS_INSUFFICIENT, digest_baseline=digest,
                                   window_start=window_start, window_end=window_end)
        try:
            qps_rows = client.query_at("HTTP_SERVER_QPS_V1", labels, window_seconds, end_ts)
            qps = float(qps_rows[0].get("value", [0, "0"])[1])
        except Exception:  # noqa: BLE001 qps/错误率缺失不阻断基线(p95 为判定信号)
            qps = 0.0
        try:
            err_rows = client.query_at("HTTP_SERVER_ERROR_RATE_V1", labels,
                                       window_seconds, end_ts)
            error_rate = float(err_rows[0].get("value", [0, "0"])[1])
        except Exception:  # noqa: BLE001 无 5xx 样本为合法空集
            error_rate = 0.0
    except Exception:  # noqa: BLE001 Prometheus 不可达 → 未封存,可重试
        logger.exception("baseline capture: run %s 健康窗口采集失败", run.id)
        return BaselineCapture(status=STATUS_CAPTURE_FAILED, digest_baseline=digest)

    return BaselineCapture(
        status=STATUS_OK, digest_baseline=digest,
        healthy_metrics={"p95_ms": p95_ms, "qps": qps, "error_rate": error_rate,
                         "sample_count": samples},
        window_start=window_start, window_end=window_end)


def _to_naive_utc(dt: datetime) -> datetime:
    from datetime import timezone
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)
