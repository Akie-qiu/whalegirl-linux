"""Evidence-driven backend regressions; synthetic inputs and no external sends."""
import ast
import asyncio
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.upstream.ai_engine import AIEngine, http_error_metadata
from whalegirl_linux.model_reply import guarded_chat
from whalegirl_linux.delivery import reconcile_outbound
from whalegirl_linux.gateway import AgentGateway
import test_context_delivery as receipts

ROOT = Path(__file__).resolve().parents[1]


def sse(*values):
    return ''.join('data: ' + (v if isinstance(v, str) else json.dumps(v)) + '\n\n' for v in values)


class EngineRegressions(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_poll_drains_native_socket_request_before_transport_close(self):
        directory = ROOT / 'test-runs'; directory.mkdir(exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix='native-close-', dir=directory))
        (directory / 'native-read.endpoint').write_text('native-read-1.sock')
        started, release = asyncio.Event(), asyncio.Event()
        cancelled = []
        async def handler(request):
            started.set()
            try:
                await release.wait()
                return httpx.Response(200, json={'ok': True})
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
        gateway = AgentGateway('http://127.0.0.1:6175', 'synthetic')
        gateway.ui_jobs_directory = directory
        gateway.native_reader_endpoint = str(directory / 'native-read-1.sock')
        gateway.native_reader = httpx.AsyncClient(base_url='http://localhost', transport=httpx.MockTransport(handler))
        poll = asyncio.create_task(gateway.native_request('/messages'))
        await started.wait()
        poll.cancel()
        await asyncio.gather(poll, return_exceptions=True)
        close = asyncio.create_task(gateway.close())
        await asyncio.sleep(0)
        self.assertFalse(close.done())
        release.set()
        await close
        self.assertFalse(cancelled)
        self.assertFalse(gateway.native_requests)

    async def test_explicit_stream_token_limit_retries_once_and_accounts_for_each_attempt(self):
        bodies = iter([
            sse({'choices': [{'delta': {'content': '{"actions":["never execute"'}, 'finish_reason': 'length'}]},
                {'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 30}}, '[DONE]'),
            sse({'choices': [{'delta': {'content': '{"should_reply":false,"actions":[]}'}, 'finish_reason': 'stop'}]},
                {'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}, '[DONE]')])
        calls, events = [], []
        def answer(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, text=next(bodies), headers={'Content-Type': 'text/event-stream'})
        engine = AIEngine('https://example.test/v1', 'synthetic', model='deepseek-v4.1-flash',
            api_style='openai', reasoning_effort='medium', stream_enabled=True, max_tokens=400, max_retries=2)
        engine.usage_callback = events.append
        engine._clients[id(asyncio.get_running_loop())] = httpx.AsyncClient(transport=httpx.MockTransport(answer))
        try:
            result = await engine.chat([{'role': 'user', 'content': 'test'}])
            self.assertEqual(json.loads(result)['actions'], [])
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0]['max_tokens'], 2448)
            self.assertGreater(calls[1]['max_tokens'], calls[0]['max_tokens'])
            self.assertEqual(calls[0]['messages'], calls[1]['messages'])
            self.assertEqual([e['output_tokens'] for e in events], [30, 5])
            self.assertEqual([e['status'] for e in events], ['error', 'success'])
        finally:
            await engine.close()

    async def test_tool_decision_uses_nonstream_once_with_global_streaming_enabled(self):
        calls = []
        def answer(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls', 'message': {
                'tool_calls': [{'id': 'wait', 'type': 'function', 'function': {
                    'name': 'wait_for_next_message', 'arguments': '{}'}}]}}]})
        engine = AIEngine('https://example.test/v1', 'synthetic', api_style='openai', stream_enabled=True)
        engine._clients[id(asyncio.get_running_loop())] = httpx.AsyncClient(transport=httpx.MockTransport(answer))
        try:
            result = await engine.chat_with_tools([], [{'type': 'function', 'function': {
                'name': 'wait_for_next_message', 'parameters': {'type': 'object', 'properties': {}}}}])
            self.assertEqual(len(calls), 1)
            self.assertFalse(calls[0].get('stream', False))
            self.assertTrue(engine.stream_enabled)
            self.assertEqual(result['tool_calls'][0]['function']['name'], 'wait_for_next_message')
        finally:
            await engine.close()

    async def test_proactive_format_guard_reuses_valid_output_without_another_call(self):
        class Engine:
            last_error = ''
            async def chat(self, *args, **kwargs):
                raise AssertionError('Valid proactive output must not call another model')
        diagnostics = {}
        result = await guarded_chat(Engine(), [], diagnostics=diagnostics, stage='proactive_decision',
            initial_raw='{"should_reply":false,"actions":[]}')
        self.assertFalse(json.loads(result)['should_reply'])
        self.assertNotIn('format_repair_attempts', diagnostics)

    async def test_proactive_format_guard_repairs_only_once(self):
        class Engine:
            last_error = ''
            calls = 0
            async def chat(self, *args, **kwargs):
                self.calls += 1
                return '{"should_reply":false,"actions":[]}'
        engine, diagnostics = Engine(), {}
        result = await guarded_chat(engine, [], diagnostics=diagnostics, stage='proactive_decision', initial_raw='{broken')
        self.assertFalse(json.loads(result)['should_reply'])
        self.assertEqual(engine.calls, 1)
        self.assertEqual(diagnostics['format_repair_attempts'], 1)


class InputGuards(unittest.TestCase):
    def test_upstream_diagnostic_does_not_copy_provider_echo_or_key(self):
        response = httpx.Response(403, json={'error': {'code': 'permission_denied', 'param': 'tools',
            'message': 'sk-SYNTHETIC-SECRET private user message'}})
        value = http_error_metadata(response)
        self.assertEqual(value, {'http_status': 403, 'provider_error_code': 'permission_denied', 'provider_parameter': 'tools'})
        self.assertNotIn('SYNTHETIC', json.dumps(value))

    def test_contradictory_debounce_is_rejected_and_zero_remains_disabled(self):
        with self.assertRaisesRegex(ValueError, '消息合并总等待上限'):
            Settings(debounce_single_seconds=.3, debounce_burst_seconds=.9, debounce_max_seconds=.1)
        self.assertEqual(Settings(debounce_max_seconds=0).debounce_max_seconds, 0)

    def test_legacy_debounce_loads_without_rewriting_private_config(self):
        runs = ROOT / 'test-runs'; runs.mkdir(exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix='debounce-legacy-', dir=runs))
        values = Settings().model_dump()
        values.update(debounce_single_seconds=.3, debounce_burst_seconds=.9, debounce_max_seconds=.1)
        path = directory / 'settings.json'
        path.write_text(json.dumps(values), encoding='utf-8')
        before = path.read_bytes()
        config = Config(directory)
        self.assertEqual(config.settings.debounce_max_seconds, .9)
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, '消息合并总等待上限'):
            config.save(config.settings.model_copy(update={'debounce_max_seconds': .1}))


class StaleReceiptTests(receipts.ReceiptLifecycleTests):
    async def test_runtime_offers_wait_tool_under_global_stream_setting(self):
        event = self.event(); self.queue(event)
        engine = await self.runtime.model()
        engine.api_style = 'openai'
        engine.stream_enabled = True
        offered = []
        async def tools(messages, definitions, **options):
            offered.extend(t['function']['name'] for t in definitions)
            return {'content': '{"should_reply":false,"actions":[]}', 'tool_calls': []}
        engine.chat_with_tools = tools
        await self.runtime.process_next()
        self.assertIn('wait_for_next_message', offered)
        self.assertFalse(self.gateway.sent)

    async def test_stale_sending_is_reconciled_without_restart_or_another_send(self):
        event = self.event(); self.queue(event)
        identity = self.pending(event)
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE outbound_actions SET state='sending',sent_at=? WHERE id=?", (time.time()-200, identity))
        rows = [{'localId': 7, 'serverId': 77, 'isSelf': True, 'type': 47, 'nativeStatus': 2,
                 'sticker': {'md5': 'a'*32}}]
        self.assertEqual(len(reconcile_outbound(self.store, 'account', event['chat'], rows)), 1)
        self.assertEqual(self.store.actions(event['key'])[0]['state'], 'sent')
        self.assertEqual(self.gateway.sent, [])

    async def test_active_sending_is_not_reconciled_before_grace_period(self):
        event = self.event(); self.queue(event)
        identity = self.pending(event)
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE outbound_actions SET state='sending' WHERE id=?", (identity,))
        rows = [{'localId': 7, 'serverId': 77, 'isSelf': True, 'type': 47, 'nativeStatus': 2,
                 'sticker': {'md5': 'a'*32}}]
        self.assertEqual(reconcile_outbound(self.store, 'account', event['chat'], rows), [])
        self.assertEqual(self.store.actions(event['key'])[0]['state'], 'sending')


class ChildReaperTests(unittest.TestCase):
    def test_orphans_reap_without_losing_tracked_server_exit_code(self):
        tree = ast.parse((ROOT / 'deploy/gateway_entry.py').read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'reap_children')
        # Load only the pure helper, never the container bootstrap in this host.
        module = ast.Module(body=[function], type_ignores=[])
        namespace = {'os': SimpleNamespace(waitpid=None, WNOHANG=1, waitstatus_to_exitcode=lambda status: status >> 8)}
        exec(compile(module, '<reaper-test>', 'exec'), namespace)
        server = SimpleNamespace(pid=10, returncode=None)
        with patch.object(namespace['os'], 'waitpid', side_effect=[(20, 0), (10, 7 << 8), (0, 0)]):
            self.assertEqual(namespace['reap_children']([server]), [20, 10])
        self.assertEqual(server.returncode, 7)


if __name__ == '__main__':
    unittest.main()
