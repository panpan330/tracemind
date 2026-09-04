"""V2.1-A:Alertmanager webhook 严格 Schema(Pydantic)。

严格性:字段类型强校验(labels/annotations 值必须 str、startsAt 必须可解析时间、
fingerprint 必填);顶层未知键 ignore(兼容 Alertmanager 4/5 差异)——
边界校验(alertname 白名单/标签长度)在 fingerprint.normalize_labels 与端点层执行。
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AlertmanagerAlert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["firing", "resolved"]
    labels: dict[str, str]
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: datetime
    endsAt: Optional[datetime] = None
    generatorURL: Optional[str] = None
    fingerprint: str = Field(min_length=1, max_length=64)


class AlertmanagerWebhookIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: str = "4"
    groupKey: str = ""
    status: Literal["firing", "resolved"]
    receiver: str = ""
    groupLabels: dict[str, str] = Field(default_factory=dict)
    alerts: list[AlertmanagerAlert] = Field(min_length=1)
