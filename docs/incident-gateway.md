# Incident Gateway:Alertmanager 接入层(V2.1-A)

> 本文是 V2.1-A 的启动与运维文档;聚合/调度/自动调查属 V2.1-B(未实现)。

## 链路

```text
Prometheus(规则 OrderOperationP95High,P95>100ms 持续 15s,demo 初始值)
    → Alertmanager(route → webhook,send_resolved: true)
    → POST /api/integrations/alertmanager/webhook(Bearer Token)
    → 鉴权 → 边界校验 → delivery_hash 幂等落库 alert_event
    → alert_instance 投影(FIRING → RESOLVED 单向,version CAS)
```

V2.1-A 阶段**只落库与投影**:不创建 Incident、不启动 Agent Run
(响应中 `created_incidents`/`updated_incidents` 恒为空数组;聚合/queued Run/Dispatcher 属 V2.1-B)。

## 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `TRACEMIND_ALERTMANAGER_WEBHOOK_TOKEN` | *(空 = 禁用)* | Webhook Bearer Token;空值时端点返回 403 |
| `TRACEMIND_ALERTMANAGER_ALERTNAME_ALLOWLIST` | `OrderOperationP95High` | 逗号分隔 alertname 白名单 |
| `TRACEMIND_ALERTMANAGER_MAX_BODY_BYTES` | `65536` | 请求体大小上限(超限 413) |
| `TRACEMIND_ALERTMANAGER_MAX_ALERTS` | `20` | 单批 alerts 数量上限(超限 422) |

- compose demo:`alertmanager.yml` 内的 Bearer credentials 与 ai-service 的
  `TRACEMIND_ALERTMANAGER_WEBHOOK_TOKEN` 均为 demo 占位值 `demo-am-token-2026`;
  真实部署用受控凭据渲染,不提交密钥。
- 标签白名单(白名单外键剥离,不入库):`alertname/service/operation/environment/severity/instance/job`;
  任一保留值超长(>128 字符)→ 该条告警 ignored。

## 数据表(migration 010)

- `alert_event`:不可变投递日志;唯一键 `(source, delivery_hash)` —— 精确重放
  (同 payload 重试)只落一条,重放计数 `events.duplicates`。
- `alert_instance`:当前状态投影;`alert_instance_key = source:fingerprint:startsAt`;
  `FIRING → RESOLVED` 单向推进(version 递增);迟到旧 FIRING 只归档事件、不回退投影。

## 幂等与状态语义

| 场景 | 行为 |
|---|---|
| 同 payload 精确重放 N 次 | alert_event 1 条,instance version 不变,响应 `events.duplicates = N-1` |
| FIRING → RESOLVED | version +1,记录 resolved_at |
| RESOLVED 后收到 FIRING | 事件归档,投影不回退(不重新打开) |
| resolved-only 且无既有实例 | 事件归档 + 建 RESOLVED 投影(无任何下游动作) |
| 未知 alertname / 超长标签 | 该条 ignored(不落库),响应 `ignored` 计数 |

## 启动(compose demo)

```bash
docker compose up -d --build alertmanager prometheus jaeger   # jaeger 已随默认栈启动
docker compose config   # 校验
# Prometheus UI: http://<host>:9090/rules 可见 OrderOperationP95High
```

主机侧混合部署(既定边界):ai-service 在宿主机运行时,Alertmanager 需能访问
`ai-service:8000`(compose 内)或将 webhook url 指向宿主机地址;Prometheus 抓取目标
按 `observability/prometheus.yml` 调整。

## 手工验证

```bash
curl -s -X POST http://localhost:8000/api/integrations/alertmanager/webhook \
  -H "Authorization: Bearer demo-am-token-2026" -H "Content-Type: application/json" \
  -d '{"version":"4","status":"firing","receiver":"tracemind","alerts":[{"status":"firing","labels":{"alertname":"OrderOperationP95High","service":"order-service","operation":"ORDER_CREATE","environment":"demo"},"annotations":{"summary":"smoke"},"startsAt":"2026-09-04T05:00:00.000Z","fingerprint":"smoke001"}]}'
```

## 混合拓扑安全启动(宿主机 ai-service + VM 网关/观测)

混合部署下 ai-service 监听 `0.0.0.0:8000`(供 VM 侧 Alertmanager/Prometheus 访问),
必须用 Windows 防火墙把 8000 的入站来源限制到 VM 网段,避免向局域网/公网暴露:

```powershell
# 管理员 PowerShell:只允许 VM 地址访问(本环境 VM=192.168.88.10,网段=192.168.88.0/24)
netsh advfirewall firewall delete rule name="TraceMind ai-service 8000"
netsh advfirewall firewall add rule name="TraceMind ai-service 8000" dir=in action=allow   protocol=TCP localport=8000 remoteip=192.168.88.10/32 profile=any
```

- 实测说明:VMware VMnet8 适配器没有 NLA 网络类别(Get-NetConnectionProfile 不列出),
  `profile=private` 的规则**不会匹配**该适配器流量,因此 Profile 保持 any、
  用 RemoteIP 收紧来源(如需更宽,可放宽到 192.168.88.0/24)。
- 若 VM 地址变化(DHCP),同步更新防火墙规则的 remoteip。
- 鉴权不因此放松:webhook 仍强制 Bearer Token;三个条件(0.0.0.0 绑定 + 防火墙
  来源收紧 + token)必须同时满足,缺一不可。

## V2.1-B 待办(未实现)

Incident 聚合事务、`queued` Run 与 Dispatcher、`incident_alert` 关联、
自动调查启动、历史基线窗口(`startsAt-10m ~ startsAt-1m`)、`alert_status`/`lifecycle_status` 拆分。
