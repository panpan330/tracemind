# -*- coding: utf-8 -*-
"""一次性探针:负载运行中直接调用 recovery_signal 的真实实现。用后即删。"""
from datetime import datetime, timedelta, timezone

from app.services import recovery_signal as rs

now = datetime.now(timezone.utc).replace(tzinfo=None)
signal_at = now - timedelta(seconds=70)   # 70 秒前的信号 → 60s 窗口已可完全落在信号后
print("探针信号时刻:", signal_at, "| now:", now)

# 负载由外部启动(调用方先起 loadgen),这里轮询观察 3 个周期
out = rs.measure_post_signal_p95("order-service", "ORDER_CREATE", signal_at,
                                 baseline=None, wait_seconds=100)
print("status:", out.status)
print("source:", out.source, "| threshold:", out.threshold_ms, "| p95:", out.p95_ms)
print("sample_count(请求增量):", out.sample_count, "| post_signal_window:", out.post_signal_window)
print("reason:", out.reason)
