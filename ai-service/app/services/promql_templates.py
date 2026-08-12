"""固定 PromQL 模板注册表(V1.4 冻结;标签契约见 spec §4.1)。
指标名基于 Micrometer 实际输出(http_server_requests_seconds_*)。"""

TEMPLATES = {
    "HTTP_SERVER_P95_V1": {
        "expr": ('histogram_quantile(0.95, sum by (le) ('
                 'rate(http_server_requests_seconds_bucket{service=~"%(service)s",'
                 'uri=~"%(uri)s",%(method)s%(status)s}[%(window)s])))'),
    },
    "HTTP_SERVER_QPS_V1": {
        "expr": ('sum(rate(http_server_requests_seconds_count{service=~"%(service)s",'
                 'uri=~"%(uri)s",%(method)s%(status)s}[%(window)s]))'),
    },
    "HTTP_SERVER_ERROR_RATE_V1": {
        "expr": ('sum(rate(http_server_requests_seconds_count{service=~"%(service)s",'
                 'uri=~"%(uri)s",status=~"5.."}[%(window)s])) / '
                 '(sum(rate(http_server_requests_seconds_count{service=~"%(service)s",'
                 'uri=~"%(uri)s"}[%(window)s])) + 1e-9)'),
    },
}
