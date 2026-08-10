"""健康指标基线采集:Incident 创建时从 Java 观测端点取修复前健康 P95。"""
import httpx

from app.config import settings


def capture_health_baseline(service_ref: str) -> dict | None:
    """调用 Java 内部观测端点。Java 未启动/异常/P95 缺失时返回 None(调用方容错)。"""
    url = f"{settings.inventory_service_url}/internal/observations/metrics?window_seconds=300"
    try:
        resp = httpx.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    p95 = data.get("p95_ms")
    if p95 is None:  # Java 端点返回驼峰 p95Ms
        p95 = data.get("p95Ms")
    if p95 is None:
        return None
    return {"p95_ms": int(p95), "qps": data.get("qps"), "error_rate": data.get("error_rate")}
