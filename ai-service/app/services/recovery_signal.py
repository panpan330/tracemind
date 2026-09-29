"""V2.1-C:恢复信号 —— 与告警同口径的 HTTP P95(唯一实现)。

为什么单独成模块:自愈判定(图内 resolved_recheck)、通用恢复验证
(recovery_service.verify_recovery)与锁场景独立验证器(mysql_blocking_transaction)
必须使用**完全相同的口径**,否则同一 Incident 会出现"一处说恢复、一处说未恢复"。

口径(硬约束):
1. 指标 = 与告警相同的 `HTTP_SERVER_P95_V1` 固定模板 + 服务/操作取冻结 RunContext
   (自动 Run 即服务端映射的告警标签),operation 决定 uri 正则;禁止通配 uri。
2. **窗口必须完全位于恢复信号之后**(`at_time - window >= signal_at`):
   滚动 rate 查询的结果时间戳只说明"最后一次抓取时刻",不代表窗口内样本都在信号之后。
3. 信号后必须有足够新请求(`sample_count >= recovery_min_requests`),否则
   INCONCLUSIVE —— 没有流量就不能宣称"指标已恢复"。
4. 阈值来源:合格基线(quality='OK')→ `baseline.p95_ms × 1.2`;否则显式 SLO
   (`settings.slo_p95_ms`)。两者都不可判定 → INCONCLUSIVE。
5. 不宣告恢复也不宣告失败的情形一律 INCONCLUSIVE,由调用方转人工。
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import settings
from app.services.operation_registry import uri_regex_for_operation

logger = logging.getLogger(__name__)

STATUS_RECOVERED = "recovered"
STATUS_NOT_RECOVERED = "not_recovered"
STATUS_INCONCLUSIVE = "INCONCLUSIVE"

SOURCE_BASELINE = "baseline"
SOURCE_SLO = "SLO"

P95_RECOVERY_RATIO = 1.2


@dataclass
class RecoverySignal:
    status: str                        # recovered | not_recovered | INCONCLUSIVE
    source: str | None = None          # baseline | SLO
    threshold_ms: float | None = None
    p95_ms: float | None = None
    sample_count: int = 0
    window_seconds: int = 0
    evaluated_at: datetime | None = None
    latest_sample_at: int | None = None
    post_signal_window: bool = False
    reason: str | None = None
    query_template_id: str = "HTTP_SERVER_P95_V1"
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        out = {"status": self.status, "source": self.source,
               "thresholdMs": self.threshold_ms, "p95Ms": self.p95_ms,
               "sampleCount": self.sample_count,
               "windowSeconds": self.window_seconds,
               "postSignalWindow": self.post_signal_window,
               "queryTemplateId": self.query_template_id}
        if self.reason:
            out["reason"] = self.reason
        if self.evaluated_at:
            out["evaluatedAt"] = int(_epoch_utc(self.evaluated_at))
        if self.latest_sample_at:
            out["latestSampleAt"] = self.latest_sample_at
        out.update(self.extra)
        return out


def _epoch_utc(naive_utc: datetime) -> float:
    """naive-UTC datetime → epoch 秒(显式按 UTC 解释,禁止本地时区歧义)。"""
    from datetime import timezone as _tz
    return naive_utc.replace(tzinfo=_tz.utc).timestamp()


def _client():
    """可注入的观测客户端(测试 monkeypatch)。"""
    from app.services.prometheus_client import PrometheusMetricsClient
    return PrometheusMetricsClient()


def _labels(service_ref: str, operation_ref: str | None, window_seconds: int) -> dict:
    return {"service": service_ref,
            "uri": uri_regex_for_operation(service_ref, operation_ref),
            "extra": "", "window": f"{window_seconds}s"}


def threshold_for(baseline: dict | None) -> tuple[float, str]:
    """阈值来源:合格基线 ×1.2 优先,否则显式 SLO(绝不"默认通过")。"""
    base_p95 = (baseline or {}).get("p95_ms")
    if base_p95:
        return float(base_p95) * P95_RECOVERY_RATIO, SOURCE_BASELINE
    return float(settings.slo_p95_ms), SOURCE_SLO


def _evaluate(samples: int, p95_ms: float, baseline: dict | None,
              window_seconds: int, evaluated_at: datetime,
              latest_sample_at: int | None, post_signal: bool) -> RecoverySignal:
    if samples < settings.recovery_min_requests:
        return RecoverySignal(
            status=STATUS_INCONCLUSIVE, sample_count=samples,
            window_seconds=window_seconds, evaluated_at=evaluated_at,
            latest_sample_at=latest_sample_at, post_signal_window=post_signal,
            reason="insufficient_post_signal_requests" if post_signal
            else "insufficient_samples")
    threshold, source = threshold_for(baseline)
    recovered = p95_ms <= threshold
    return RecoverySignal(
        status=STATUS_RECOVERED if recovered else STATUS_NOT_RECOVERED,
        source=source, threshold_ms=threshold, p95_ms=p95_ms,
        sample_count=samples, window_seconds=window_seconds,
        evaluated_at=evaluated_at, latest_sample_at=latest_sample_at,
        post_signal_window=post_signal)


def _measure(service_ref: str, operation_ref: str | None, *,
             signal_at: datetime | None, baseline: dict | None,
             now: datetime, wait_seconds: float, require_post_signal: bool
             ) -> RecoverySignal:
    window_seconds = settings.recovery_signal_window_s
    deadline = time.monotonic() + max(0.0, wait_seconds)
    last = None
    while True:
        evaluated_at = now
        if require_post_signal and signal_at is not None:
            if evaluated_at - timedelta_seconds(window_seconds) < signal_at:
                # 窗口仍覆盖信号前流量 → 等待窗口完全落在信号之后
                last = RecoverySignal(
                    status=STATUS_INCONCLUSIVE, window_seconds=window_seconds,
                    evaluated_at=evaluated_at, post_signal_window=False,
                    reason="post_signal_window_incomplete")
                if time.monotonic() >= deadline:
                    return last
                time.sleep(settings.recovery_poll_interval_s)
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                continue
        labels = _labels(service_ref, operation_ref, window_seconds)
        end_ts = _epoch_utc(evaluated_at)
        start_ts = end_ts - window_seconds
        try:
            client = _client()
            samples = client.sample_count("HTTP_SERVER_REQ_COUNT_V1", labels,
                                          start_ts, end_ts)
        except ValueError as exc:
            if str(exc) == "METRICS_NOT_FOUND":
                # 窗口内无任何序列(该时段无流量)→ 样本按 0 计,走"请求不足"分支
                samples = 0
            else:
                logger.exception("恢复信号采集失败 service=%s op=%s",
                                 service_ref, operation_ref)
                return RecoverySignal(status=STATUS_INCONCLUSIVE,
                                      window_seconds=window_seconds,
                                      evaluated_at=evaluated_at,
                                      post_signal_window=require_post_signal,
                                      reason="metrics_unavailable")
        except Exception:  # noqa: BLE001 观测后端不可用 → 不宣告任何结论
            logger.exception("恢复信号采集失败 service=%s op=%s",
                             service_ref, operation_ref)
            return RecoverySignal(status=STATUS_INCONCLUSIVE,
                                  window_seconds=window_seconds,
                                  evaluated_at=evaluated_at,
                                  post_signal_window=require_post_signal,
                                  reason="metrics_unavailable")
        if samples < settings.recovery_min_requests:
            out = RecoverySignal(
                status=STATUS_INCONCLUSIVE, sample_count=samples,
                window_seconds=window_seconds, evaluated_at=evaluated_at,
                post_signal_window=require_post_signal,
                reason="insufficient_post_signal_requests")
            if time.monotonic() < deadline and require_post_signal:
                last = out
                time.sleep(settings.recovery_poll_interval_s)
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                continue
            return out
        try:
            rows = client.query_at("HTTP_SERVER_P95_V1", labels, window_seconds, end_ts)
            p95_ms = float(rows[0].get("value", [0, "0"])[1]) * 1000.0
            latest = int(float(rows[0].get("value", [0, 0])[0]))
        except Exception:  # noqa: BLE001 查询失败 → INCONCLUSIVE
            logger.exception("恢复信号 P95 查询失败 service=%s op=%s",
                             service_ref, operation_ref)
            return RecoverySignal(status=STATUS_INCONCLUSIVE,
                                  sample_count=samples,
                                  window_seconds=window_seconds,
                                  evaluated_at=evaluated_at,
                                  post_signal_window=require_post_signal,
                                  reason="metrics_unavailable")
        return _evaluate(samples, p95_ms, baseline, window_seconds, evaluated_at,
                         latest, require_post_signal)


def timedelta_seconds(seconds: int):
    from datetime import timedelta
    return timedelta(seconds=seconds)


def measure_post_signal_p95(service_ref: str, operation_ref: str | None,
                            signal_at: datetime, *, baseline: dict | None,
                            now: datetime | None = None,
                            wait_seconds: float | None = None) -> RecoverySignal:
    """恢复判定:只接受**完全位于信号之后**的窗口样本(有界等待窗口填满)。"""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    wait = settings.recovery_signal_wait_s if wait_seconds is None else wait_seconds
    return _measure(service_ref, operation_ref, signal_at=signal_at,
                    baseline=baseline, now=now, wait_seconds=wait,
                    require_post_signal=True)


def measure_current_p95(service_ref: str, operation_ref: str | None, *,
                        baseline: dict | None,
                        now: datetime | None = None) -> RecoverySignal:
    """执行前复核用:当前最新窗口是否仍异常(无"信号后"约束——问题就是"现在是否已自愈")。"""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return _measure(service_ref, operation_ref, signal_at=None, baseline=baseline,
                    now=now, wait_seconds=0, require_post_signal=False)
