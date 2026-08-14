"""上下文压缩:证据超阈值时把最旧证据的 content 摘要成一行关键结论。"""


def _key_metric(ev: dict) -> str:
    c = ev.get("content") or {}
    if not isinstance(c, dict):
        return f"passed={ev.get('passed')}"
    for k, label in (("p95Ms", "p95"), ("wait_duration_ms", "wait_ms"),
                     ("index_present", "index")):
        if k in c:
            return f"{label}={c[k]}"
    return f"passed={ev.get('passed')}"


def summarize(evidence: list[dict], max_keep: int = 8) -> list[dict]:
    if len(evidence) <= max_keep:
        return list(evidence)
    out = []
    for i, ev in enumerate(evidence):
        e = dict(ev)
        if i < len(evidence) - max_keep:
            e["content"] = _key_metric(ev)   # 最旧的压缩成一行关键结论
        out.append(e)
    return out
