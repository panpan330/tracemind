"""OpenAI 兼容端点聊天客户端(httpx 直调)。
provider 适配(bailian 发 enable_thinking)、tool_calls 解析、429/5xx 退避重试、usage。"""
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class LLMClientError(Exception):
    pass


def prompt_hash(content: str) -> str:
    """基于最终渲染内容的稳定短 hash(脱敏后计算,算法版本 sha256-16)。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ChatResult:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict = field(default_factory=dict)
    model: str | None = None


class LLMClient:
    RETRY_STATUS = {429, 500, 502, 503, 504}
    NO_RETRY_STATUS = {400, 401, 403, 404}

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, provider: str | None = None,
                 timeout: float = 30.0) -> None:
        self.base_url = (base_url or settings.chat_base_url_resolved).rstrip("/")
        self.api_key = api_key or settings.chat_api_key_resolved
        self.model = model or settings.chat_model_resolved
        self.provider = provider or settings.chat_provider
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _payload(self, messages: list[dict], tools: list[dict] | None,
                 max_tokens: int, model: str | None) -> dict:
        payload: dict[str, Any] = {
            "model": model or self.model, "messages": messages, "max_tokens": max_tokens,
        }
        if self.provider == "bailian":
            payload["enable_thinking"] = False
        if tools:
            payload["tools"] = tools
        return payload

    def _parse(self, resp: httpx.Response) -> ChatResult:
        data = resp.json()
        choice = data["choices"][0]
        msg = choice.get("message", {})
        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments", "{}"))
                tool_calls.append(ToolCall(id=tc.get("id", ""), name=tc["function"]["name"],
                                           arguments=args if isinstance(args, dict) else {}))
            except (json.JSONDecodeError, KeyError):
                logger.warning("tool_calls arguments 解析失败,丢弃: %r", tc.get("function"))
        usage = data.get("usage", {})
        return ChatResult(
            content=msg.get("content"),
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason"),
            usage={"input_tokens": usage.get("prompt_tokens"),
                   "output_tokens": usage.get("completion_tokens")},
            model=data.get("model"),
        )

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             max_tokens: int = 600, model: str | None = None) -> ChatResult | None:
        if not (self.api_key and self.base_url and self.model):
            raise LLMClientError("LLM 未配置(base_url/api_key/model 至少一项为空)")
        payload = self._payload(messages, tools, max_tokens, model)
        for attempt in range(3):  # 最多 3 次总尝试(首次 + ≤2 重试)
            try:
                resp = httpx.post(f"{self.base_url}/chat/completions",
                                  headers=self._headers(), json=payload,
                                  timeout=self.timeout)
                if resp.status_code in self.RETRY_STATUS and attempt < 2:
                    time.sleep(1 << attempt)
                    continue
                if resp.status_code in self.NO_RETRY_STATUS:
                    logger.warning("LLM 返回不可重试状态 %s", resp.status_code)
                    return None
                resp.raise_for_status()
                return self._parse(resp)
            except httpx.HTTPError as exc:
                if attempt < 2:
                    time.sleep(1 << attempt)
                    continue
                logger.warning("LLM chat 调用失败(第 %d 次): %s", attempt + 1, exc)
                return None

    def chat_json(self, messages: list[dict], max_tokens: int = 600,
                  model: str | None = None) -> dict | None:
        result = self.chat(messages, max_tokens=max_tokens, model=model)
        if result is None or not result.content:
            return None
        return self._extract_json(result.content)

    def chat_json_with_usage(self, messages: list[dict], max_tokens: int = 600,
                             model: str | None = None):
        """同 chat_json,但顺带返回 usage/finish_reason(供 model_call 审计)。"""
        result = self.chat(messages, max_tokens=max_tokens, model=model)
        if result is None or not result.content:
            return None, {}, None
        return self._extract_json(result.content), result.usage or {}, result.finish_reason

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """健壮 JSON 提取:容忍 markdown fence / 前后说明文字(真实模型常见行为)。"""
        import re
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            logger.warning("LLM 返回无 JSON 对象: %.200s", text)
            return None
        try:
            parsed = json.loads(m.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            logger.warning("LLM 返回 JSON 解析失败: %.200s", text)
            return None
