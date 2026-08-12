"""TraceNormalizer(TRACE_NORMALIZER_V1):Jaeger span → Agent 稳定证据结构。"""
import datetime

NORMALIZER_VERSION = "TRACE_NORMALIZER_V1"
ERROR_TRACE_INCOMPLETE = "TRACE_INCOMPLETE"

# 稳定语义字段 + 旧字段兼容映射(仅 Fixture/迁移;agent 配 SEMCONV_STABILITY_OPT_IN 后输出稳定字段)
_SEMCONV_LEGACY_MAP = {"db.system.name": "db.system", "db.operation.name": "db.operation"}


def _attr(tags: dict, key: str):
    if key in tags:
        return tags[key]
    return tags.get(_SEMCONV_LEGACY_MAP.get(key))


class TraceNormalizer:
    def normalize(self, trace: dict, operation_ref: str) -> dict:
        spans = trace.get("spans") or []
        by_id = {s.get("spanId"): s for s in spans}
        servers = [s for s in spans
                   if s.get("kind") == "SPAN_KIND_SERVER"
                   and s.get("process", {}).get("serviceName") == "inventory-service"
                   and not str(s.get("operationName", "")).startswith("/internal/")]
        if not servers:
            return {"status": ERROR_TRACE_INCOMPLETE, "normalizationRuleVersion": NORMALIZER_VERSION}
        server = sorted(servers, key=lambda s: s.get("duration", 0), reverse=True)[0]
        server_ms = server.get("duration", 0) / 1000.0
        db_spans = []
        for s in spans:
            if s.get("kind") != "SPAN_KIND_CLIENT":
                continue
            tags = s.get("tags") or {}
            if _attr(tags, "db.system.name") != "mysql":
                continue
            if _attr(tags, "db.operation.name") not in ("SELECT", "UPDATE"):
                continue
            if not self._is_descendant(s, server, by_id):
                continue
            db_spans.append(s)
        if not db_spans:
            return {"status": ERROR_TRACE_INCOMPLETE, "normalizationRuleVersion": NORMALIZER_VERSION}
        target = max(db_spans, key=lambda s: s.get("duration", 0))
        db_ms = target.get("duration", 0) / 1000.0
        ratio = (db_ms / server_ms) if server_ms > 0 else 0.0
        start_us = trace.get("startTime") or 0
        return {
            "status": "ok",
            "inventoryServerDurationMs": round(server_ms),
            "targetDbDurationMs": round(db_ms),
            "dbDominanceRatio": round(ratio, 2),
            "targetDbSpanId": target.get("spanId"),
            "traceId": trace.get("traceID"),
            "traceStart": _iso(start_us),
            "traceEnd": _iso(start_us + (server.get("duration", 0) or 0)),
            "normalizationRuleVersion": NORMALIZER_VERSION,
        }

    @staticmethod
    def _is_descendant(span, ancestor, by_id):
        cur = span.get("parentSpanId")
        seen = 0
        while cur and seen < 100:
            if cur == ancestor.get("spanId"):
                return True
            parent = by_id.get(cur)
            if not parent:
                return False
            cur = parent.get("parentSpanId")
            seen += 1
        return False


def _iso(epoch_us: int) -> str:
    return datetime.datetime.fromtimestamp(epoch_us / 1_000_000,
                                           tz=datetime.timezone.utc).isoformat()
