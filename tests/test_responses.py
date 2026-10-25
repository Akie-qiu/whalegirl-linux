"""Responses protocol integration; mocked HTTP only, no real chat sends."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

import httpx

from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.upstream.ai_engine import AIEngine


USAGE = {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130,
         "input_tokens_details": {"cached_tokens": 70},
         "output_tokens_details": {"reasoning_tokens": 10}}


class ResponsesPayloadTests(unittest.TestCase):
    def test_endpoint_resolution_preserves_gateway_prefix(self):
        for url, style, expected in [
            ("https://example.test", "responses", "https://example.test/v1/responses"),
            ("https://example.test/v1/", "responses", "https://example.test/v1/responses"),
            ("https://example.test/chat/completions", "responses", "https://example.test/responses"),
            ("https://example.test/proxy/v1/chat/completions", "responses", "https://example.test/proxy/v1/responses"),
            ("https://example.test/v1/messages", "responses", "https://example.test/v1/responses"),
            ("https://example.test/anthropic/responses/", "auto", "https://example.test/anthropic/responses"),
            ("https://example.test/v1/responses", "openai", "https://example.test/v1/chat/completions"),
            ("https://example.test/v1", "auto", "https://example.test/v1/chat/completions"),
        ]:
            with self.subTest(url=url, style=style):
                engine = AIEngine(url, "test", api_style=style)
                self.assertEqual(engine.api_url, expected)
                if expected.endswith("/responses"):
                    self.assertEqual(engine.api_style, "responses")

    def test_text_images_history_and_cache_prefix_layout(self):
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "历史\n【本次需要回复的消息】\n看这张图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA==", "detail": "low"}},
        ]}, {"role": "assistant", "content": "之前的回复"},
            {"role": "user", "content": "接着说"}]
        before = copy.deepcopy(messages)
        engine = AIEngine("https://example.test/v1/responses", "test", model="same-model")
        payload = engine._build_payload(messages, "人设\n【本轮动态上下文】动态", .55, max_tokens=450)
        self.assertEqual(messages, before)
        self.assertEqual(payload["model"], "same-model")
        self.assertEqual(payload["instructions"], "人设")
        self.assertEqual(payload["max_output_tokens"], 450)
        self.assertIs(payload["store"], False)
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("messages", payload)
        self.assertNotIn("temperature", payload)
        self.assertIn("【本轮动态上下文】动态", payload["input"][0]["content"][0]["text"])
        self.assertEqual(payload["input"][0]["content"][0]["type"], "input_text")
        self.assertEqual(payload["input"][0]["content"][1], {
            "type": "input_image", "image_url": "data:image/png;base64,AA==", "detail": "low"})
        self.assertEqual(payload["input"][1:], messages[1:])

    def test_protocol_persists_without_changing_model_or_key(self):
        runs = Path(__file__).resolve().parents[1] / "test-runs"
        runs.mkdir(exist_ok=True)
        config = Config(Path(tempfile.mkdtemp(prefix="responses-config-", dir=runs)))
        config.save(Settings(api_url="https://example.test/chat/completions", model="same-model"), "test-key")
        config.save(config.settings.model_copy(update={"api_style": "responses", "reasoning_effort": "low", "model_stream_enabled": True}))
        reloaded = Config(config.directory)
        self.assertEqual(reloaded.settings.api_style, "responses")
        self.assertEqual(reloaded.settings.model, "same-model")
        self.assertEqual(reloaded.api_key(), "test-key")
        self.assertEqual(reloaded.public()["api_style"], "responses")
        self.assertEqual(Settings().api_style, "auto")
        self.assertEqual(reloaded.settings.reasoning_effort, "low")
        self.assertTrue(reloaded.settings.model_stream_enabled)


class ResponsesHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events, self.requests = [], []
        self.engine = AIEngine("https://example.test/v1/responses", "test", max_retries=3)
        self.engine.usage_callback = self.events.append

    def transport(self, handler):
        def handle(request):
            self.requests.append(request)
            return handler(request)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        self.engine._clients[id(asyncio.get_running_loop())] = client
        self.engine._get_client = AsyncMock(return_value=client)

    async def asyncTearDown(self):
        await self.engine.close()

    async def test_json_response_and_numeric_usage(self):
        self.transport(lambda _: httpx.Response(200, json={
            "status": "completed", "usage": USAGE, "output": [
                {"type": "reasoning", "summary": [{"text": "must not be returned"}]},
                {"type": "message", "role": "assistant", "content": [
                    {"type": "output_text", "text": '{"actions":[]}', "annotations": []}]},
            ]}))
        result = await self.engine.chat([{"role": "user", "content": "只输出 JSON"}],
                                       response_format={"type": "json_object"})
        self.assertEqual(result, '{"actions":[]}')
        self.assertEqual(len(self.requests), 1)
        payload = json.loads(self.requests[0].content)
        self.assertEqual(payload["text"], {"format": {"type": "json_object"}})
        self.assertNotIn("response_format", payload)
        self.assertEqual(len(self.events), 1)
        self.assertEqual([self.events[0][k] for k in (
            "input_tokens", "output_tokens", "total_tokens", "cached_tokens", "uncached_tokens", "reasoning_tokens"
        )], [100, 30, 130, 70, 30, 10])
        self.assertEqual(self.engine.usage.total_tokens, 130)

    async def test_json_schema_shape(self):
        self.transport(lambda _: httpx.Response(200, json={"output": [
            {"type": "message", "content": [{"type": "output_text", "text": '{"ok":true}'}]}]}))
        schema = {"name": "reply", "strict": True, "schema": {
            "type": "object", "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"], "additionalProperties": False}}
        await self.engine.chat([{"role": "user", "content": "test"}],
                               response_format={"type": "json_schema", "json_schema": schema})
        self.assertEqual(json.loads(self.requests[0].content)["text"]["format"],
                         {"type": "json_schema", **schema})

    async def test_incomplete_output_is_not_sent_or_retried(self):
        self.transport(lambda _: httpx.Response(200, json={"status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"}, "usage": USAGE, "output": [
                {"type": "message", "content": [{"type": "output_text", "text": '{"act'}]}]}))
        self.assertEqual(await self.engine.chat([{"role": "user", "content": "test"}]), "")
        self.assertIn("Token", self.engine.last_error)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.events[0]["status"], "error")
        self.assertEqual(self.events[0]["total_tokens"], 130)

    async def test_bad_request_does_not_retry(self):
        self.transport(lambda _: httpx.Response(400, json={"error": {"message": "unsupported model"}}))
        self.assertEqual(await self.engine.chat([{"role": "user", "content": "test"}]), "")
        self.assertEqual(self.engine.last_error, "HTTP 400")
        self.assertEqual(len(self.requests), 1)

    async def test_empty_output_does_not_repeat_paid_request(self):
        self.transport(lambda _: httpx.Response(200, json={"status": "completed", "output": [], "usage": USAGE}))
        self.assertEqual(await self.engine.chat([{"role": "user", "content": "test"}]), "")
        self.assertIn("未返回文本", self.engine.last_error)
        self.assertEqual(len(self.requests), 1)

    async def test_stream_delta_and_terminal_cache_usage(self):
        chunks = [{"type": "response.created", "response": {"status": "in_progress"}},
                  {"type": "response.reasoning_summary_text.delta", "delta": "hidden"},
                  {"type": "response.output_text.delta", "delta": "连"},
                  {"type": "response.output_text.delta", "delta": "通"},
                  {"type": "response.completed", "response": {"usage": USAGE}}]
        body = "".join("event: " + c["type"] + "\ndata: " + json.dumps(c) + "\n\n" for c in chunks)
        self.transport(lambda _: httpx.Response(200, text=body))
        text = "".join([chunk async for chunk in self.engine.chat_stream([{"role": "user", "content": "test"}])])
        self.assertEqual(text, "连通")
        self.assertTrue(json.loads(self.requests[0].content)["stream"])
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0]["cached_tokens"], 70)
        self.assertEqual(self.events[0]["status"], "success")

    async def test_stream_failure_is_recorded(self):
        body = 'data: ' + json.dumps({"type": "response.failed", "response": {"usage": USAGE}}) + '\n\n'
        self.transport(lambda _: httpx.Response(200, text=body))
        self.assertEqual([c async for c in self.engine.chat_stream([{"role": "user", "content": "test"}])], [])
        self.assertEqual(self.events[0]["status"], "error")
        self.assertTrue(self.engine.last_error)

    async def test_shared_engine_factories_receive_protocol(self):
        from whalegirl_linux.runtime import Runtime
        from test_core import FakeEngine, FakeGateway, ROOT
        from whalegirl_linux.store import Store
        folder = Path(tempfile.mkdtemp(prefix="responses-runtime-", dir=ROOT / "test-runs"))
        config = Config(folder)
        config.save(Settings(account_id="account", api_style="responses", reasoning_effort="low", model_stream_enabled=True), "test-key")
        seen = []
        def factory(**kwargs):
            seen.append(kwargs)
            return FakeEngine(**kwargs)
        runtime = Runtime(config, Store(folder / "state.sqlite3"), FakeGateway(), ROOT / "styles",
                          engine_factory=factory)
        try:
            await runtime.model()
            await runtime.images.new_engine().close()
            await runtime.tieba.new_engine("account").close()
            self.assertEqual(len(seen), 3)
            self.assertTrue(all(v["api_style"] == "responses" for v in seen))
            self.assertTrue(all(v["reasoning_effort"] == "low" and v["stream_enabled"] for v in seen))
        finally:
            await runtime.close()


if __name__ == "__main__":
    unittest.main()
