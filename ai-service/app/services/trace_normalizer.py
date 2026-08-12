"""TraceNormalizer(TRACE_NORMALIZER_V1):Jaeger span → Agent 稳定证据结构。
兼容:
- Jaeger HTTP JSON API(spanID/parentSpanID/tags 数组/processID→processes map,无 kind 字段)
- Fixture 格式(kind=SPAN_KIND_* + process.serviceName dict)
判定采用结构推断(不依赖 kind):inventory HTTP SERVER span + 其后代 MySQL SELECT/UPDATE CLIENT span。"""
import datetime

NORMALIZER_VERSION = "TRACE_NORMALIZER_V1"
ERROR_TRACE_INCOMPLETE = "TRACE_INCOMPLETE"

# 稳定语义字段 + 旧字段兼容映射(仅 Fixture/迁移;agent 配 SEMCONV_STABILITY_OPT_IN 后输出稳定字段)
_SEMCONV_LEGACY_MAP = {"db.system.name": "db.system", "db.operation.name": "db.operation"}


def _attr(tags: dict | list, key: str):
    """兼容 dict 与 Jaeger tags 数组([{"key": ..., "value": ...}])。"""
    if isinstance(tags, list):
        for t in tags:
            if t.get("key") == key:
                return t.get("value")
        return None
    if key in tags:
        return tags[key]
    return tags.get(_SEMCONV_LEGACY_MAP.get(key))


def _f(span: dict, *names):
    """字段访问:兼容 Jaeger camelCase(spanID/parentSpanID/processID)与 snake_case。"""
    for n in names:
        if n in span and span[n] is not None:
            return span[n]
    return None


def _span_service(span: dict, processes: dict) -> str:
    """Jaeger:span.processID → processes[pid].serviceName;Fixture:span.process.serviceName。"""
    proc = span.get("process") or {}
    svc = proc.get("serviceName")
    if svc:
        return svc
    pid = _f(span, "processID", "processId")
    if pid is not None:
        return (processes.get(str(pid)) or {}).get("serviceName") or ""
    return ""


def _is_inventory_server(span: dict, service: str) -> bool:
    """inventory 的 HTTP 服务端 span:service 匹配 + 业务路由(GET/POST /api/...),排除 /internal/ 与场景/管理端点。"""
    if service != "inventory-service":
        return False
    op = span.get("operationName") or ""
    if op.startswith("/internal/") or "scenario" in op or "lock-holder" in op:
        return False
    # HTTP 服务端:operationName 为 "METHOD /path"(Jaeger 由 server span 命名)
    if " " in op and op.split(" ", 1)[0] in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        return True
    # 兼容 Fixture:kind=SPAN_KIND_SERVER
    return span.get("kind") == "SPAN_KIND_SERVER"


def _is_target_db_span(span: dict) -> bool:
    """目标 DB span:MySQL SELECT/UPDATE + 目标库表(inventory / tracemind_business)。"""
    tags = span.get("tags") or {}
    op = _attr(tags, "db.operation.name")
    if op not in ("SELECT", "UPDATE"):
        return False
    sys_name = _attr(tags, "db.system.name")
    if sys_name is not None and sys_name != "mysql":
        return False
    coll = _attr(tags, "db.collection.name")
    ns = _attr(tags, "db.namespace")
    if coll is not None and coll != "inventory":
        return False
    if ns is not None and ns != "tracemind_business":
        return False
    # 兼容 Fixture:kind=SPAN_KIND_CLIENT + 无 db.collection 时仅按 operation 判定
    if span.get("kind") not in (None, "SPAN_KIND_CLIENT", "SPAN_KIND_INTERNAL"):
        return False
    return True


class TraceNormalizer:
    def normalize(self, trace: dict, operation_ref: str) -> dict:
        spans = trace.get("spans") or []
        processes = trace.get("processes") or {}
        by_id = {str(_f(s, "spanId", "spanID")): s for s in spans}
        servers = [s for s in spans if _is_inventory_server(s, _span_service(s, processes))]
        if not servers:
            return {"status": ERROR_TRACE_INCOMPLETE, "normalizationRuleVersion": NORMALIZER_VERSION}
        server = sorted(servers, key=lambda s: s.get("duration", 0), reverse=True)[0]
        server_ms = server.get("duration", 0) / 1000.0
        db_spans = [s for s in spans
                    if _is_target_db_span(s) and self._is_descendant(s, server, by_id)]
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
            "targetDbSpanId": str(_f(target, "spanId", "spanID")),
            "traceId": trace.get("traceID"),
            "traceStart": _iso(start_us),
            "traceEnd": _iso(start_us + (server.get("duration", 0) or 0)),
            "normalizationRuleVersion": NORMALIZER_VERSION,
        }

    @staticmethod
    def _is_descendant(span, ancestor, by_id):
        cur = _f(span, "parentSpanId", "parentSpanID")
        anc_id = str(_f(ancestor, "spanId", "spanID"))
        seen = 0
        while cur is not None and seen < 100:
            if str(cur) == anc_id:
                return True
            parent = by_id.get(str(cur))
            if not parent:
                return False
            cur = _f(parent, "parentSpanId", "parentSpanID")
            seen += 1
        return False


def _iso(epoch_us: int) -> str:
    return datetime.datetime.fromtimestamp(epoch_us / 1_000_000,
                                           tz=datetime.timezone.utc).isoformat()
