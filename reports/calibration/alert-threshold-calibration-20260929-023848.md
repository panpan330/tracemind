# ORDER_CREATE HTTP P95 阈值校准报告

- 生成时间:2026-09-29T02:38:48.273020+00:00
- Prometheus:http://192.168.88.10:9090
- 阈值:100 ms;最小样本数:30

## 阶段:healthy
- 样本数:51(不足下限=False)
- 下界:3.4 ms
- P50:5.2 ms
- P90:6.3 ms
- 上界:6.6 ms

## 阶段:scn001
- 样本数:51(不足下限=False)
- 下界:39.5 ms
- P50:42.5 ms
- P90:44.0 ms
- 上界:44.4 ms

## 阶段:scn002
- 样本数:30(不足下限=False)
- 下界:11381.7 ms
- P50:11381.7 ms
- P90:11381.7 ms
- 上界:11381.7 ms

## 断言
- 结论:**FAIL**(阈值 100 ms)
- 健康上界 < 阈值:PASS
- scn001 下界 > 阈值:FAIL(下界 39.5 ms)
- scn002 下界 > 阈值:PASS(下界 11381.7 ms)

## 建议配置(回填 ai-service/.env.local 与 compose)
```
TRACEMIND_BASELINE_MAX_P95_MS=10
TRACEMIND_SLO_P95_MS=10
TRACEMIND_BASELINE_MIN_SAMPLES=30
```
