"""AI 引擎：Chat Completions、Responses 和 Anthropic 兼容格式。"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import AsyncIterator
from urllib.parse import urlsplit

import httpx
from loguru import logger
from ..token_usage import normalize_usage
from ..time_context import CLOCK_RULE, clock_messages


class IncompleteModelResponse(ValueError):
    """A paid response began but cannot be safely used as an action plan."""


@dataclass
class TokenUsage:
    """Token 用量统计"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    request_count: int = 0

    def add(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += prompt + completion
        self.request_count += 1

    @property
    def summary(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "request_count": self.request_count,
        }


class AIEngine:
    """AI 引擎：按配置选择模型协议，不额外探测或重发不同协议。"""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str = "gpt-4",
        max_tokens: int = 500,
        temperature: float = 0.7,
        timeout: float = 30,
        max_retries: int = 3,
        api_style: str = "auto",
        reasoning_effort: str = "default",
        stream_enabled: bool = False,
    ):
        self.api_style = self._resolve_api_style(api_url, api_style)
        self.api_url = self._normalize_api_url(api_url, self.api_style)
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries
        self.reasoning_effort = reasoning_effort
        self.stream_enabled = stream_enabled
        self.usage = TokenUsage()
        self.usage_callback = None
        self._clients: dict[int, httpx.AsyncClient] = {}
        self._available = True
        self.last_error = ""
        self.last_first_token_ms: int | None = None

    @staticmethod
    def _resolve_api_style(api_url: str, configured: str = "auto") -> str:
        style = str(configured or "auto").strip().lower()
        if style in {"openai", "responses", "anthropic"}:
            return style
        return AIEngine._detect_api_style(api_url)

    @staticmethod
    def _normalize_api_url(api_url: str, api_style: str = "openai") -> str:
        """补全常见中转站的完整接口路径。"""
        url = api_url.strip().rstrip("/")
        if not url:
            return ""
        lowered = url.lower()
        if api_style == "responses":
            if lowered.endswith("/responses"):
                return url
            for suffix in ("/chat/completions", "/messages"):
                if lowered.endswith(suffix):
                    return url[:-len(suffix)] + "/responses"
            if lowered.endswith("/v1"):
                return url + "/responses"
            return url + "/v1/responses"
        if api_style == "anthropic":
            if lowered.endswith("/v1/messages"):
                return url
            if lowered.endswith("/v1"):
                return url + "/messages"
            if lowered.endswith("/messages"):
                return url
            return url + "/v1/messages"

        if lowered.endswith("/responses"):
            return url[:-len("/responses")] + "/chat/completions"
        if lowered.endswith("/chat/completions"):
            return url
        if lowered.endswith("/v1"):
            return url + "/chat/completions"
        if lowered.endswith("/completions"):
            return url
        return url + "/v1/chat/completions"

    @staticmethod
    def _detect_api_style(api_url: str) -> str:
        """Infer OpenAI-compatible by default; Anthropic needs an explicit hint."""
        url = str(api_url or "").lower()
        if urlsplit(url).path.rstrip("/").endswith("/responses"):
            return "responses"
        if (
            "/v1/messages" in url
            or "api.anthropic.com" in url
            or "anthropic" in url
        ):
            return "anthropic"
        return "openai"

    @property
    def api_key_set(self) -> bool:
        return bool(str(self.api_key or "").strip())

    async def reconfigure(
        self,
        api_url: str,
        api_key: str,
        model: str,
        api_style: str = "auto",
        reasoning_effort: str = "default",
        stream_enabled: bool = False,
    ) -> None:
        """Apply WebUI settings without requiring a process restart."""
        await self.close()
        self.api_style = self._resolve_api_style(api_url, api_style)
        self.api_url = self._normalize_api_url(api_url, self.api_style)
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "").strip() or self.model
        self.reasoning_effort = reasoning_effort
        self.stream_enabled = stream_enabled
        self._available = True
        logger.info(
            "AI 配置已热更新: "
            f"style={self.api_style}, model={self.model}, url={self.api_url}"
        )

    def _get_client_for_current_loop(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        key = id(loop)
        client = self._clients.get(key)
        if client is not None and not client.is_closed:
            return client

        headers = {"Content-Type": "application/json"}
        if self.api_style == "anthropic":
            headers.update({
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            })
        else:
            headers["Authorization"] = f"Bearer {self.api_key}"

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            headers=headers,
            verify=True,
        )
        self._clients[key] = client
        return client

    async def _get_client(self) -> httpx.AsyncClient:
        return self._get_client_for_current_loop()

    @staticmethod
    def _responses_messages(messages: list[dict]) -> list[dict]:
        """Translate the shared chat/vision input without mutating its history."""
        result = []
        for message in messages:
            content = message.get("content", "")
            if isinstance(content, list):
                blocks = []
                for block in content:
                    kind = block.get("type")
                    if kind == "text":
                        blocks.append({"type": "input_text", "text": block["text"]})
                    elif kind == "image_url":
                        image = block["image_url"]
                        image = {"url": image} if isinstance(image, str) else image
                        blocks.append({"type": "input_image", "image_url": image["url"],
                                       "detail": image.get("detail", "auto")})
                    elif kind in {"input_text", "input_image", "input_file", "output_text", "refusal"}:
                        blocks.append(dict(block))
                    else:
                        raise ValueError("Unsupported Responses input block")
                content = blocks
            result.append({"role": message["role"], "content": content})
        return result

    @staticmethod
    def _responses_text(data: dict) -> str:
        # output_text is an SDK helper; HTTP responses contain typed output items.
        parts = []
        for item in data.get("output") or []:
            if item.get("type") != "message" or item.get("role", "assistant") != "assistant":
                continue
            for block in item.get("content") or []:
                if block.get("type") == "output_text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "refusal":
                    parts.append(block.get("refusal", ""))
        return "\n".join(parts).strip()

    def _build_payload(
        self,
        messages: list[dict],
        system_prompt: str,
        temperature: float | None,
        stream: bool = False,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> dict:
        payload = {
            "model": self.model,
            "max_tokens": int(max_tokens or self.max_tokens),
            "temperature": temperature if temperature is not None else self.temperature,
        }
        # Chat replies use Flash's direct-answer mode rather than its default reasoning pass.
        if self.api_style == "openai" and self.model in ("deepseek-flash", "deepseek-v4-flash", "deepseek-v4-pro"):
            payload["thinking"] = {"type": "disabled"}

        dynamic_marker = "\n【本轮动态上下文】"
        reply_marker = "\n【本次需要回复的消息】\n"
        # A tiny live input, after the reusable system prefix. All reply/tool
        # paths use the server clock, including Responses and proactive chat.
        if dynamic_marker in system_prompt:
            prefix, suffix = system_prompt.split(dynamic_marker, 1)
            system_prompt = prefix + CLOCK_RULE + dynamic_marker + suffix
        else:
            system_prompt += CLOCK_RULE
        effective_system = system_prompt
        effective_messages = messages
        if (
            dynamic_marker in system_prompt
            and messages
            and messages[0].get("role") == "user"
        ):
            static_part, dynamic_part = system_prompt.split(dynamic_marker, 1)
            dynamic_block = dynamic_marker + dynamic_part
            first_msg = messages[0]
            content = first_msg.get("content")
            if isinstance(content, str) and reply_marker in content:
                before_reply, after_reply = content.split(reply_marker, 1)
                new_content = before_reply + dynamic_block + reply_marker + after_reply
                effective_system = static_part
                effective_messages = [{"role": "user", "content": new_content}, *messages[1:]]
            elif (
                isinstance(content, list)
                and content
                and isinstance(content[0], dict)
                and content[0].get("type") == "text"
                and reply_marker in str(content[0].get("text") or "")
            ):
                before_reply, after_reply = content[0]["text"].split(reply_marker, 1)
                new_first_block = {
                    **content[0],
                    "text": before_reply + dynamic_block + reply_marker + after_reply,
                }
                effective_system = static_part
                effective_messages = [
                    {"role": "user", "content": [new_first_block, *content[1:]]},
                    *messages[1:],
                ]

        effective_messages = clock_messages(effective_messages)

        if self.api_style == "responses":
            payload["max_output_tokens"] = payload.pop("max_tokens")
            # Optional sampling controls are rejected by some reasoning models.
            payload.pop("temperature")
            # The application owns history and memory; no server-side conversation IDs.
            payload["store"] = False
            payload["input"] = self._responses_messages(effective_messages)
            if effective_system:
                payload["instructions"] = effective_system
        elif self.api_style == "anthropic":
            payload["messages"] = effective_messages
            if effective_system:
                payload["system"] = effective_system
        else:
            full_messages = []
            if effective_system:
                full_messages.append({"role": "system", "content": effective_system})
            full_messages.extend(effective_messages)
            payload["messages"] = full_messages

        if self.reasoning_effort != "default":
            if self.reasoning_effort not in {"low", "medium", "high"}:
                raise ValueError("不支持的思考强度")
            if self.api_style == "responses":
                payload["reasoning"] = {"effort": self.reasoning_effort}
            elif self.api_style == "anthropic":
                payload["output_config"] = {"effort": self.reasoning_effort}
            else:
                payload["reasoning_effort"] = self.reasoning_effort
        if response_format and self.api_style == "responses":
            format_spec = dict(response_format)
            if format_spec.get("type") == "json_schema":
                format_spec = {"type": "json_schema", **format_spec["json_schema"]}
            payload["text"] = {"format": format_spec}
        elif response_format and self.api_style != "anthropic":
            payload["response_format"] = response_format
        if stream:
            payload["stream"] = True
            if self.api_style == "openai":
                payload["stream_options"] = {"include_usage": True}
        return payload

    @staticmethod
    async def _sse_events(response):
        """Parse SSE frames, including multiline data and the final unterminated frame."""
        data_lines = []
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                value = line[5:]
                data_lines.append(value[1:] if value.startswith(" ") else value)
            elif not line and data_lines:
                data = "\n".join(data_lines)
                data_lines = []
                yield {"type": "_done"} if data.strip() == "[DONE]" else json.loads(data)
        if data_lines:
            data = "\n".join(data_lines)
            yield {"type": "_done"} if data.strip() == "[DONE]" else json.loads(data)

    async def _stream_parts(self, client, payload, usage, started):
        completed, has_text = False, False
        async with client.stream("POST", self.api_url, json=payload) as response:
            response.raise_for_status()
            try:
                async for data in self._sse_events(response):
                    if not isinstance(data, dict):
                        raise IncompleteModelResponse("模型流包含无效事件")
                    if isinstance(data.get("usage"), dict):
                        usage.update(data["usage"])
                    event = data.get("type", "")
                    content = ""
                    if event == "error" or data.get("error"):
                        raise IncompleteModelResponse("模型流返回错误，未执行未完成的回复")
                    if self.api_style == "responses":
                        if event in {"response.failed", "response.cancelled", "response.incomplete"}:
                            usage.update((data.get("response") or {}).get("usage") or {})
                            reason = ((data.get("response") or {}).get("incomplete_details") or {}).get("reason")
                            detail = "（达到回复 Token 上限）" if reason == "max_output_tokens" else ""
                            raise IncompleteModelResponse("Responses 流未完成" + detail)
                        if event == "response.completed":
                            result = data.get("response") or {}
                            usage.update(result.get("usage") or {})
                            if result.get("status", "completed") != "completed":
                                raise IncompleteModelResponse("Responses 流未完成")
                            completed = True
                            if not has_text:
                                content = self._responses_text(result)
                        elif event in {"response.output_text.delta", "response.refusal.delta"}:
                            content = data.get("delta", "")
                    elif self.api_style == "anthropic":
                        if event == "message_start":
                            usage.update((data.get("message") or {}).get("usage") or {})
                        delta = data.get("delta") or {}
                        if event == "message_delta" and delta.get("stop_reason") in {"max_tokens", "model_context_window_exceeded"}:
                            raise IncompleteModelResponse("Claude 回复未完成（达到 Token 上限）")
                        if event == "message_stop":
                            completed = True
                        if delta.get("type") == "text_delta":
                            content = delta.get("text", "")
                    else:
                        choices = data.get("choices") or []
                        if choices:
                            choice = choices[0]
                            reason = choice.get("finish_reason")
                            if reason in {"length", "content_filter"}:
                                raise IncompleteModelResponse("模型回复未完成：" + reason)
                            if reason:
                                completed = True
                            content = (choice.get("delta") or {}).get("content") or ""
                    if content:
                        if self.last_first_token_ms is None:
                            self.last_first_token_ms = round((time.monotonic() - started) * 1000)
                        has_text = True
                        yield content
                    if event == "_done" or (self.api_style == "responses" and completed) or event == "message_stop":
                        break
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                # Never promote partial JSON or repeat a request whose response has begun.
                raise IncompleteModelResponse("模型流在完成前断开，未执行未完成的回复") from exc
        if not completed:
            raise IncompleteModelResponse("模型流在完成前断开，未执行未完成的回复")

    async def chat(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        operation: str = "chat",
    ) -> str:
        """发送聊天请求，返回回复文本"""
        if not self._available:
            logger.warning("AI 引擎不可用")
            return ""

        self.last_error = ""
        self.last_first_token_ms = None

        payload = self._build_payload(
            messages,
            system_prompt,
            temperature,
            stream=self.stream_enabled,
            max_tokens=max_tokens,
            response_format=response_format,
        )

        # 重试循环
        last_error = None
        attempts = max(1, self.max_retries)
        for attempt in range(attempts):
            started,created=time.monotonic(),time.time()
            raw_usage={};outcome='error'
            try:
                client = await self._get_client()
                logger.debug(f"AI 请求: url={self.api_url}, model={self.model}, attempt={attempt + 1}")
                if self.stream_enabled:
                    content = "".join([part async for part in self._stream_parts(client, payload, raw_usage, started)])
                    if not content.strip():
                        raise IncompleteModelResponse("模型流未返回文本，请检查模型能力或回复 Token 上限")
                    outcome = 'success'
                    return content.strip()
                response = await client.post(self.api_url, json=payload)
                response.raise_for_status()

                data = response.json()
                raw_usage=data.get('usage')
                if self.api_style == "responses":
                    if data.get("status") in {"failed", "cancelled", "incomplete", "queued", "in_progress"}:
                        self.last_error = "Responses 未完成：" + data["status"]
                        if (data.get("incomplete_details") or {}).get("reason") == "max_output_tokens":
                            self.last_error += "（达到回复 Token 上限，请调高后重试）"
                        return ""
                    content = self._responses_text(data)
                    if not content:
                        self.last_error = "Responses 未返回文本，请检查模型能力或回复 Token 上限"
                        return ""
                elif self.api_style == "anthropic":
                    content = "\n".join(
                        block.get("text", "")
                        for block in data.get("content", [])
                        if block.get("type") == "text"
                    ).strip()
                else:
                    content = data["choices"][0]["message"]["content"].strip()
                outcome='success'
                logger.debug(f"AI 回复成功 (attempt {attempt + 1})")
                return content

            except IncompleteModelResponse as exc:
                self.last_error = str(exc)
                logger.warning(self.last_error)
                return ""
            except httpx.TimeoutException:
                last_error = f"请求超时 ({self.timeout}s)"
                logger.warning(f"AI 请求超时 (attempt {attempt + 1}/{attempts})")
            except httpx.HTTPStatusError as e:
                last_error = f"HTTP {e.response.status_code}"
                if e.response.status_code == 404:
                    last_error += "（模型或接口不可用，请核对模型名称和接口地址）"
                self.last_error = last_error
                logger.warning(f"AI 请求失败: {last_error} (attempt {attempt + 1}/{self.max_retries})")
                if 400 <= e.response.status_code < 500 and e.response.status_code not in (408, 409, 429):
                    logger.error("模型接口拒绝请求，请检查协议、模型、参数或凭据")
                    return ""
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                logger.warning(f"AI 请求异常: {last_error} (attempt {attempt + 1}/{self.max_retries})")
            finally:
                self.record_usage(raw_usage,started=started,created=created,status=outcome,operation=operation)

            # 指数退避
            if attempt < attempts - 1:
                wait = 2 ** attempt
                await asyncio.sleep(wait)

        logger.error(f"AI 请求最终失败: {last_error}")
        self.last_error = last_error or "模型返回空内容"
        return ""

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        system_prompt: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        operation: str = "chat",
        tool_choice: str = "auto",
    ) -> dict:
        """Return a complete non-streamed tool decision with usage accounting."""
        if not self._available:
            return {"content": "", "tool_calls": [], "assistant_message": {}}
        if self.api_style != "openai":
            raise ValueError("工具调用仅支持 Chat Completions 接口")
        if self.stream_enabled:
            raise ValueError("接话等待工具需要关闭流式接收")

        self.last_error = ""
        self.last_first_token_ms = None
        payload = self._build_payload(
            messages,
            system_prompt,
            temperature,
            stream=False,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice
        started, created = time.monotonic(), time.time()
        raw_usage = {}
        outcome = "error"
        try:
            client = await self._get_client()
            response = await client.post(self.api_url, json=payload)
            response.raise_for_status()
            data = response.json()
            raw_usage = data.get("usage") or {}
            choice = (data.get("choices") or [{}])[0]
            if choice.get("finish_reason") in {"length", "content_filter"}:
                raise IncompleteModelResponse("模型工具调用未完成：" + choice["finish_reason"])
            message = choice.get("message") or {}
            assistant_message = {
                key: message[key] for key in ("role", "content", "tool_calls", "refusal", "reasoning_content")
                if key in message
            }
            content = message.get("content")
            if not isinstance(content, str):
                content = ""
            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list):
                tool_calls = []
            if any(not isinstance(call, dict) or not call.get("id")
                   or not isinstance(call.get("function"), dict)
                   or not isinstance(call["function"].get("arguments"), str)
                   for call in tool_calls):
                raise IncompleteModelResponse("模型工具调用格式不完整，未执行动作")
            assistant_message.setdefault("role", "assistant")
            outcome = "success"
            if self.last_first_token_ms is None:
                self.last_first_token_ms = round((time.monotonic() - started) * 1000)
            return {
                "content": content.strip(),
                "tool_calls": tool_calls,
                "assistant_message": assistant_message,
            }
        except IncompleteModelResponse as exc:
            self.last_error = str(exc)
        except httpx.TimeoutException:
            self.last_error = f"请求超时 ({self.timeout}s)"
        except httpx.HTTPStatusError as exc:
            self.last_error = f"HTTP {exc.response.status_code}"
            if exc.response.status_code == 404:
                self.last_error += "（模型或接口不可用，请核对模型名称和接口地址）"
            logger.warning("模型工具调用请求失败: " + self.last_error)
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            logger.warning("模型工具调用请求异常: " + self.last_error)
        finally:
            self.record_usage(raw_usage, started=started, created=created,
                              status=outcome, operation=operation)
        return {"content": "", "tool_calls": [], "assistant_message": {}}

    def record_usage(self,raw_usage=None, *, started,created,status='success',operation='chat'):
        """Accounting must never turn a completed model call into a retry."""
        try:
            normalized=normalize_usage(raw_usage,self.api_style)
            if operation=='chat':
                self.usage.add(normalized['input_tokens'] or 0,normalized['output_tokens'] or 0)
            if self.usage_callback:
                self.usage_callback({'created':created,'provider':urlsplit(self.api_url).hostname or '',
                    'model':self.model,'operation':operation,'status':status,
                    'elapsed_ms':round((time.monotonic()-started)*1000),**normalized})
        except Exception as exc:
            logger.warning('Token统计记录失败: '+type(exc).__name__)

    async def chat_stream(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        operation: str = "chat",
    ) -> AsyncIterator[str]:
        """流式聊天请求，逐块返回"""
        if not self._available:
            return
        self.last_error = ""
        self.last_first_token_ms = None
        payload = self._build_payload(
            messages,
            system_prompt,
            temperature,
            stream=True,
            max_tokens=max_tokens,
            response_format=response_format,
        )

        started,created=time.monotonic(),time.time();raw_usage={};outcome='error'
        try:
            client = await self._get_client()
            async for content in self._stream_parts(client, payload, raw_usage, started):
                yield content
            outcome = 'success'
        except Exception as e:
            self.last_error = str(e) if isinstance(e, IncompleteModelResponse) else "流式请求失败：" + type(e).__name__
            logger.error(self.last_error)
        finally:
            self.record_usage(raw_usage,started=started,created=created,status=outcome,operation=operation)

    async def test_connection(self) -> tuple[bool, str]:
        """测试 API 连接"""
        try:
            response = await self.chat(
                messages=[{"role": "user", "content": "Hi, respond with 'OK' only."}],
            )
            if response:
                return True, f"连接成功: {response[:50]}"
            return False, "未收到回复"
        except Exception as e:
            return False, f"连接失败: {e}"

    async def close(self) -> None:
        clients = list(self._clients.items())
        self._clients.clear()
        current_loop_id = id(asyncio.get_running_loop())
        for loop_id, client in clients:
            if loop_id != current_loop_id:
                continue
            if not client.is_closed:
                await client.aclose()

    @property
    def is_available(self) -> bool:
        return self._available

    @is_available.setter
    def is_available(self, value: bool) -> None:
        self._available = value
        logger.info(f"AI 引擎 {'可用' if value else '不可用'}")
