"""V1.11 成本统计:按模型聚合 model_call 的 token 与估算成本。"""
# 每百万 token 单价(元);按百炼公开价配置,可覆盖。未配置模型成本记 0。
MODEL_PRICE_PER_M = {
    "qwen3.8-max": 20.0,
    "qwen3.7-max": 20.0,
    "qwen3.7-flash": 0.5,
    "deepseek-v4-flash-0731": 1.0,
}


def aggregate_model_costs(calls: list[dict]) -> dict:
    """按模型聚合:调用次数 / input_tokens / output_tokens / 估算成本(元)。
    calls: model_call 查询结果(list[dict],含 model/input_tokens/output_tokens)。"""
    out: dict[str, dict] = {}
    for c in calls:
        m = c.get("model") or "unknown"
        item = out.setdefault(m, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0})
        item["calls"] += 1
        item["input_tokens"] += c.get("input_tokens") or 0
        item["output_tokens"] += c.get("output_tokens") or 0
    for m, item in out.items():
        unit = MODEL_PRICE_PER_M.get(m)
        if unit:
            total = float(item["input_tokens"]) + float(item["output_tokens"])
            item["cost"] = round(unit * total / 1_000_000, 6)
    return out
