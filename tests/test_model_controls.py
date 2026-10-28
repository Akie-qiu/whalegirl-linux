"""Model controls and streamed action plans; no real chat sends."""
import asyncio
import json
import unittest

import httpx

from whalegirl_linux.upstream.ai_engine import AIEngine


def sse(*events):
    return "".join("data: " + (e if isinstance(e, str) else json.dumps(e)) + "\n\n" for e in events)


class ModelControlPayloadTests(unittest.TestCase):
    def test_effort_is_opt_in_and_uses_the_selected_protocol(self):
        for style, field, value in [
            ("responses", "reasoning", {"effort": "low"}),
            ("anthropic", "output_config", {"effort": "low"}),
            ("openai", "reasoning_effort", "low"),
        ]:
            with self.subTest(style=style):
                engine = AIEngine("https://example.test/v1", "test", model="same-model", api_style=style)
                default = engine._build_payload([], "", None)
                self.assertFalse(any(k in default for k in ("reasoning", "output_config", "reasoning_effort")))
                engine.reasoning_effort = "low"
                payload = engine._build_payload([], "", None, stream=True)
                self.assertEqual(payload[field], value)
                self.assertEqual(payload["model"], "same-model")
                self.assertIs(payload["stream"], True)
                if style == "openai":
                    self.assertEqual(payload["stream_options"], {"include_usage": True})


class StreamedChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests, self.events, self.engines = [], [], []

    async def asyncTearDown(self):
        for engine in self.engines:
            await engine.close()

    def engine(self, style, body, status=200, **kwargs):
        engine = AIEngine("https://example.test/v1", "test", api_style=style,
                          stream_enabled=True, reasoning_effort="low", **kwargs)
        def handler(request):
            self.requests.append(request)
            return httpx.Response(status, text=body, headers={"Content-Type": "text/event-stream"})
        engine._clients[id(asyncio.get_running_loop())] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        engine.usage_callback = self.events.append
        self.engines.append(engine)
        return engine

    async def test_responses_returns_only_complete_json_and_keeps_usage(self):
        usage = {"input_tokens": 100, "output_tokens": 12,
                 "input_tokens_details": {"cached_tokens": 80},
                 "output_tokens_details": {"reasoning_tokens": 2}}
        engine = self.engine("responses", sse(
            {"type": "response.reasoning_summary_text.delta", "delta": "never expose"},
            {"type": "response.output_text.delta", "delta": '{"actions":'},
            {"type": "response.output_text.delta", "delta": "[]}"},
            {"type": "response.completed", "response": {"usage": usage}}))
        result = await engine.chat([], response_format={"type": "json_object"})
        self.assertEqual(json.loads(result), {"actions": []})
        payload = json.loads(self.requests[0].content)
        self.assertEqual(payload["text"], {"format": {"type": "json_object"}})
        self.assertEqual(self.events[0]["cached_tokens"], 80)
        self.assertEqual(self.events[0]["reasoning_tokens"], 2)
        self.assertEqual(self.events[0]["status"], "success")
        self.assertIsNotNone(engine.last_first_token_ms)

    async def test_valid_json_without_completion_is_not_executed_or_retried(self):
        engine = self.engine("responses", sse({"type": "response.output_text.delta", "delta": '{"actions":[]}'}))
        self.assertEqual(await engine.chat([]), "")
        self.assertIn("断开", engine.last_error)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.events[0]["status"], "error")

    async def test_incomplete_terminal_keeps_paid_usage(self):
        engine = self.engine("responses", sse(
            {"type": "response.output_text.delta", "delta": '{"actions":[]}'},
            {"type": "response.incomplete", "response": {
                "incomplete_details": {"reason": "max_output_tokens"},
                "usage": {"input_tokens": 100, "output_tokens": 20}}}))
        self.assertEqual(await engine.chat([]), "")
        self.assertIn("Token", engine.last_error)
        self.assertEqual(self.events[0]["output_tokens"], 20)
        self.assertEqual(len(self.requests), 1)

    async def test_anthropic_stream_hides_thinking_and_combines_cache_usage(self):
        engine = self.engine("anthropic", sse(
            {"type": "message_start", "message": {"usage": {"input_tokens": 2,
                "cache_read_input_tokens": 3, "cache_creation_input_tokens": 4}}},
            {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "hidden"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": '{"ok":true}'}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 6}},
            {"type": "message_stop"}))
        self.assertEqual(await engine.chat([]), '{"ok":true}')
        self.assertEqual([self.events[0][k] for k in ("input_tokens", "cached_tokens", "cache_write_tokens", "output_tokens")], [9, 3, 4, 6])

    async def test_chat_completions_handles_empty_choices_usage_chunk(self):
        engine = self.engine("openai", sse(
            {"choices": [{"delta": {"reasoning_content": "hidden"}}]},
            {"choices": [{"delta": {"content": '{"ok":true}'}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 4,
                "prompt_tokens_details": {"cached_tokens": 7}}}, "[DONE]"))
        self.assertEqual(await engine.chat([]), '{"ok":true}')
        self.assertEqual(self.events[0]["cached_tokens"], 7)
        self.assertEqual(self.events[0]["status"], "success")

    async def test_truncated_anthropic_and_chat_completions_are_rejected(self):
        for style, body in [
            ("anthropic", sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "partial"}},
                              {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}})),
            ("openai", sse({"choices": [{"delta": {"content": "partial"}, "finish_reason": "length"}]})),
        ]:
            with self.subTest(style=style):
                engine = self.engine(style, body)
                self.assertEqual(await engine.chat([]), "")
                self.assertEqual(self.events[-1]["status"], "error")
        self.assertEqual(len(self.requests), 2)

    async def test_rejected_parameter_does_not_trigger_fallback_or_retry(self):
        engine = self.engine("responses", '{"error":"unsupported reasoning"}', status=400)
        self.assertEqual(await engine.chat([]), "")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(engine.last_error, "HTTP 400")

    async def test_zero_retries_still_makes_one_attempt(self):
        engine = self.engine("responses", sse({"type": "response.completed", "response": {"output": [
            {"type": "message", "content": [{"type": "output_text", "text": '{"ok":true}'}]}]}}), max_retries=0)
        self.assertEqual(await engine.chat([]), '{"ok":true}')
        self.assertEqual(len(self.requests), 1)

class ToolCallTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_completions_returns_tool_call_and_usage(self):
        events=[]
        body={
            "choices":[{
                "message":{
                    "role":"assistant","content":None,"reasoning_content":"internal only",
                    "tool_calls":[{
                        "id":"call_wait_1","type":"function",
                        "function":{"name":"wait_for_next_message","arguments":"{}"},
                    }],
                },
                "finish_reason":"tool_calls",
            }],
            "usage":{"prompt_tokens":20,"completion_tokens":3,"total_tokens":23},
        }
        engine=AIEngine("https://example.test/chat/completions","test",model="deepseek-flash",api_style="openai")
        requests=[]
        engine._clients[id(asyncio.get_running_loop())]=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request:(requests.append(request) or httpx.Response(200,json=body))
            )
        )
        engine.usage_callback=events.append
        result=await engine.chat_with_tools([{"role":"user","content":"我先说一下"}],[{
            "type":"function","function":{"name":"wait_for_next_message","parameters":{"type":"object","properties":{}}}
        }],system_prompt="只输出 JSON")
        payload=json.loads(requests[0].content)
        self.assertEqual(payload["tool_choice"],"auto")
        self.assertEqual(payload["tools"][0]["function"]["name"],"wait_for_next_message")
        self.assertFalse(payload.get("stream",False))
        self.assertEqual(result["tool_calls"][0]["id"],"call_wait_1")
        self.assertEqual(result["assistant_message"]["reasoning_content"],"internal only")
        self.assertEqual(result["content"],"")
        self.assertEqual(events[0]["output_tokens"],3)
        self.assertEqual(events[0]["status"],"success")
        await engine.close()


if __name__ == "__main__":
    unittest.main()
