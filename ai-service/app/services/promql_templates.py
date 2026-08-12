"""固定 PromQL 模板注册表(V1.4 冻结;标签契约见 spec §4.1)。
指标名基于 Micrometer 实际输出(http_server_requests_seconds_*)。
注意:labels 拼装由调用方提供完整片段,避免空占位产生语法错误。"""

TEMPLATES = {
    "HTTP_SERVER_P95_V1": {
        "expr": ('histogram_quantile(0.95, sum by (le) ('
                 'rate(http_server_requests_seconds_bucket{service=~"%(service)s",'
                 'uri=~"%(uri)s"%(extra)s}[%(window)s])))'),
        "queryType": "instant",
    },
    "HTTP_SERVER_QPS_V1": {
        "expr": ('sum(rate(http_server_requests_seconds_count{service=~"%(service)s",'
                 'uri=~"%(uri)s"%(extra)s}[%(window)s]))'),
        "queryType": "instant",
    },
    "HTTP_SERVER_ERROR_RATE_V1": {
        "expr": ('sum(rate(http_server_requests_seconds_count{service=~"%(service)s",'
                 'uri=~"%(uri)s",status=~"5.."%(extra)s}[%(window)s])) / '
                 '(sum(rate(http_server_requests_seconds_count{service=~"%(service)s",'
                 'uri=~"%(uri)s"%(extra)s}[%(window)s])) + 1e-9)'),
        "queryType": "instant",
    },
}
