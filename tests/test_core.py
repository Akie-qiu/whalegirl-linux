import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from whalegirl_linux.app import create_app
from whalegirl_linux.gateway import AgentGateway, Receipt
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store
from whalegirl_linux.upstream.ai_engine import AIEngine, TokenUsage

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "test-runs"
RUNS.mkdir(exist_ok=True)


def data_dir():
    # Keep test files for inspection; no cleanup/deletion command is run.
    return Path(tempfile.mkdtemp(prefix="case-", dir=RUNS))


def raw(mid=1, content="鲸鱼娘你好", **changes):
    return {"localId": mid, "serverId": mid, "type": 1, "content": content,
            "sender": "tester", "senderName": "测试", "isSelf": False,
            "timestamp": time.time(), **changes}


class FakeGateway:
    def __init__(self):
        self.account = "wxid-test"
        self.rows = []
        self.sent = []
        self.confirm = True
        self.login = {"state": "idle"}

    async def status(self):
        return {"status": "logged_in", "loggedInUser": self.account}

    async def messages(self, chat):
        return self.rows

    async def send(self, chat, text):
        self.sent.append((chat, text))
        return Receipt(self.confirm, "verified-1", "不确定")

    async def chats(self):
        return [{"id": "group@chatroom", "name": "测试群", "isGroup": True}]

    async def start_login(self):
        return self.login

    async def close(self):
        pass


class FakeEngine:
    def __init__(self, **kwargs):
        self.last_error = ""
        self.usage = TokenUsage()
        self.reply = "收到，今天过得怎么样？"
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.reply

    async def close(self):
        pass


class CoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = data_dir()
        self.config = Config(self.folder)
        self.config.save(Settings(enabled=True, account_id="wxid-test", allowed_chat_ids=["group@chatroom"]), "test-secret")
        self.store = Store(self.folder / "state.sqlite3")
        self.gateway = FakeGateway()
        self.runtime = Runtime(self.config, self.store, self.gateway, ROOT / "styles", engine_factory=FakeEngine)

    async def asyncTearDown(self):
        await self.runtime.close()

    async def test_initial_history_is_only_baseline(self):
        self.gateway.rows = [raw()]
        await self.runtime.poll_once()
        self.assertIsNone(self.store.queue())
        self.assertEqual(self.store.stats(), {"baseline": 1})

    async def test_new_message_sent_once_and_history_saved(self):
        await self.runtime.poll_once()
        self.gateway.rows = [raw()]
        await self.runtime.poll_once()
        await self.runtime.poll_once()
        await self.runtime.process_next()
        await self.runtime.process_next()
        self.assertEqual(len(self.gateway.sent), 1)
        self.assertEqual(self.store.stats().get("sent"), 1)
        self.assertEqual(len(self.store.context("wxid-test", "group@chatroom", 5)), 2)

    async def test_group_without_trigger_does_not_reply(self):
        await self.runtime.poll_once()
        self.gateway.rows = [raw(content="大家晚上好")]
        await self.runtime.poll_once()
        self.assertIsNone(self.store.queue())

    async def test_self_media_old_and_ambiguous_sender_ignored(self):
        await self.runtime.poll_once()
        self.gateway.rows = [raw(1, isSelf=True), raw(2, type=3), raw(3, timestamp=time.time()-120), raw(4, isSelf=None)]
        await self.runtime.poll_once()
        self.assertIsNone(self.store.queue())

    async def test_unicode_casefold_keyword(self):
        s = self.config.settings
        event = self.runtime.event("wxid-test", "group@chatroom", raw(content="ＤＥＥＰＳＥＥＫ 你好"))
        self.assertTrue(self.runtime.should_reply(raw(content="ＤＥＥＰＳＥＥＫ 你好"), event, s))

    async def test_account_switch_pauses(self):
        self.gateway.account = "another-account"
        await self.runtime.poll_once()
        self.assertIn("账号", self.runtime.paused_reason)
        self.assertIsNone(self.store.queue())

    async def test_disable_during_generation_prevents_send(self):
        await self.runtime.poll_once()
        self.gateway.rows = [raw()]
        await self.runtime.poll_once()
        engine = await self.runtime.model()

        async def reply(messages, **kwargs):
            self.config.save(self.config.settings.model_copy(update={"enabled": False}))
            return "不会发送这条"

        engine.chat = reply
        await self.runtime.process_next()
        self.assertEqual(self.gateway.sent, [])
        self.assertEqual(self.store.stats().get("skipped"), 1)

    async def test_switch_account_before_send_prevents_send(self):
        await self.runtime.poll_once()
        self.gateway.rows = [raw()]
        await self.runtime.poll_once()
        engine = await self.runtime.model()

        async def reply(messages, **kwargs):
            self.gateway.account = "different-account"
            return "不会发给另一个账号"

        engine.chat = reply
        await self.runtime.process_next()
        self.assertEqual(self.gateway.sent, [])
        self.assertEqual(self.store.stats().get("failed"), 1)

    async def test_worker_send_exception_becomes_uncertain(self):
        await self.runtime.poll_once()
        self.gateway.rows = [raw()]
        await self.runtime.poll_once()

        async def broken_send(*args):
            raise RuntimeError("模拟发送期间连接断开")

        self.gateway.send = broken_send
        worker = asyncio.create_task(self.runtime._worker_loop())
        await asyncio.sleep(0.05)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        self.assertEqual(self.store.stats().get("uncertain"), 1)
        self.assertTrue(self.runtime.paused_reason)

    async def test_402_pauses_without_sending(self):
        await self.runtime.poll_once()
        self.gateway.rows = [raw()]
        await self.runtime.poll_once()
        engine = await self.runtime.model()
        engine.reply = ""
        engine.last_error = "HTTP 402"
        await self.runtime.process_next()
        self.assertEqual(self.gateway.sent, [])
        self.assertIn("余额", self.runtime.paused_reason)

    async def test_uncertain_send_never_auto_retries(self):
        await self.runtime.poll_once()
        self.gateway.rows = [raw()]
        await self.runtime.poll_once()
        self.gateway.confirm = False
        await self.runtime.process_next()
        await self.runtime.process_next()
        self.assertEqual(len(self.gateway.sent), 1)
        self.assertEqual(self.store.stats().get("uncertain"), 1)
        self.assertTrue(self.runtime.paused_reason)

    async def test_burst_messages_merged(self):
        await self.runtime.poll_once()
        self.gateway.rows = [raw(1), raw(2, "鲸鱼娘再说一句")]
        await self.runtime.poll_once()
        await self.runtime.process_next()
        self.assertEqual(len(self.gateway.sent), 1)
        self.assertEqual(self.store.stats().get("merged"), 1)
        self.assertEqual(len((await self.runtime.model()).calls[0]), 1)

    async def test_restart_marks_inflight_uncertain(self):
        other_dir = data_dir()
        path = other_dir / "store.db"
        a = Store(path)
        event = self.runtime.event("wxid-test", "g", raw())
        a.insert(event, "sending")
        a.close()
        b = Store(path)
        self.assertEqual(b.get(event["key"])["state"], "uncertain")
        self.assertIsNone(b.queue())
        b.close()

    async def test_secrets_encrypted_and_not_exposed(self):
        self.assertNotIn(b"test-secret", (self.folder / "api-key.enc").read_bytes())
        self.assertNotIn("test-secret", json.dumps(self.config.public()))
        self.assertEqual(Config(self.folder).api_key(), "test-secret")

    async def test_old_version_pin_cannot_be_changed(self):
        with self.assertRaises(ValidationError):
            Settings(expected_wechat_version="latest")


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(data_dir(), gateway=FakeGateway(), engine_factory=FakeEngine, run_background=False)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.headers = {"Authorization": "Bearer " + self.app.state.config.admin_token}

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def test_auth_and_origin_boundaries(self):
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")
        self.assertEqual(health.json()["failed_background_tasks"], 0)
        self.assertEqual(self.client.get("/api/config").status_code, 401)
        self.assertEqual(self.client.get("/api/config", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.post("/api/model/test", headers={**self.headers, "Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/config", headers={**self.headers, "Host": "evil.example"}).status_code, 403)

    def test_cannot_enable_without_explicit_chats(self):
        value = Settings(enabled=True).model_dump()
        r = self.client.post("/api/config", headers=self.headers, json={"settings": value, "api_key": "secret"})
        self.assertEqual(r.status_code, 400)

    def test_selected_pinned_candidate_version_matches_health_and_state(self):
        config = self.app.state.config
        config.save(Settings(expected_wechat_version="4.1.1.8"))
        self.assertEqual(self.client.get("/health").json()["wechat_version"], "4.1.1.8")
        self.assertEqual(self.client.get("/api/state", headers=self.headers).json()["wechat_pinned_version"], "4.1.1.8")

    def test_authorized_account_bound_on_enable(self):
        value = Settings(enabled=True, allowed_chat_ids=["group@chatroom"]).model_dump()
        r = self.client.post("/api/config", headers=self.headers, json={"settings": value, "api_key": "secret"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["account_id"], "wxid-test")
        self.assertNotIn("api_key", r.json())

    def test_manual_review_does_not_resend(self):
        store = self.app.state.runtime.store
        event = Runtime.event("wxid-test", "g", raw())
        store.insert(event, "uncertain")
        store.update(event["key"], "uncertain", reply="你好")
        r = self.client.post("/api/review", headers=self.headers, json={"key": event["key"], "received": False})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["automatic_resend"])
        self.assertEqual(self.app.state.runtime.gateway.sent, [])


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_qt_filler_warning_recovers_after_core_restart(self):
        warning = "当前微信版本过低，暂无法登录，请前往商店或微信官网更新到最新版本。"
        popup = {"role": "filler", "children": [
            {"role": "label", "name": "Tip"},
            {"role": "label", "name": warning},
            {"role": "push-button", "name": "OK", "children": [{"role": "label", "name": "OK"}]}]}
        tree = {"role": "desktop-frame", "children": [{"role": "application", "children": [
            {"role": "frame", "name": "Weixin", "children": [{"role": "label", "name": "Entering"}]}, popup]}]}
        requests = []

        async def handle(request):
            requests.append((request.method, request.url.path))
            return httpx.Response(200, json={"tree": tree} if request.url.path.endswith("a11y") else
                                  {"status": "logged_out", "loggedInUser": None})

        client = httpx.AsyncClient(base_url="http://127.0.0.1:6175", transport=httpx.MockTransport(handle))
        gateway = AgentGateway("http://127.0.0.1:6175", "test", client=client)
        gateway.login["qrDataUrl"] = "expired-qr"
        self.assertEqual((await gateway.status())["status"], "logged_out")
        self.assertEqual(gateway.login, {"state": "attention", "message": warning, "qrDataUrl": ""})
        self.assertEqual(requests, [("GET", "/api/status/auth"), ("GET", "/api/debug/a11y")])
        await gateway.close()

    async def test_login_warning_is_preserved_without_confirmation_click(self):
        requests = []
        tree = {"role": "desktop-frame", "children": [{"role": "dialog", "children": [
            {"role": "label", "name": "请核对客户端提示"}, {"role": "push-button", "name": "OK"}]}]}

        async def handle(request):
            requests.append((request.method, request.url.path))
            if request.url.path.endswith("/login"):
                return httpx.Response(200, json={"qrDataUrl": "test-image"})
            return httpx.Response(200, json={"tree": tree})

        client = httpx.AsyncClient(base_url="http://127.0.0.1:6175", transport=httpx.MockTransport(handle))
        gateway = AgentGateway("http://127.0.0.1:6175", "test", client=client)
        await gateway._observe_login()
        self.assertEqual(gateway.login["state"], "attention")
        self.assertEqual(gateway.login["message"], "请核对客户端提示")
        self.assertEqual(requests, [("POST", "/api/status/login"), ("GET", "/api/debug/a11y")])
        await gateway.close()

    async def test_timeout_send_is_confirmed_by_readback_without_second_post(self):
        posts = 0

        async def handle(request):
            nonlocal posts
            if request.method == "POST":
                posts += 1
                raise httpx.ReadTimeout("timeout", request=request)
            return httpx.Response(200, json=[raw(7, "回复", isSelf=True)] if posts else [])

        client = httpx.AsyncClient(base_url="http://127.0.0.1:6175", transport=httpx.MockTransport(handle))
        gateway = AgentGateway("http://127.0.0.1:6175", "test", client=client)
        result = await gateway.send("g", "回复")
        self.assertTrue(result.confirmed)
        self.assertEqual(posts, 1)
        await gateway.close()

    async def test_existing_same_text_is_not_false_confirmation(self):
        posts = 0

        async def handle(request):
            nonlocal posts
            if request.method == "POST":
                posts += 1
                return httpx.Response(200, json={"success": True})
            return httpx.Response(200, json=[raw(7, "回复", isSelf=True)])

        client = httpx.AsyncClient(base_url="http://127.0.0.1:6175", transport=httpx.MockTransport(handle))
        gateway = AgentGateway("http://127.0.0.1:6175", "test", client=client)
        with patch("whalegirl_linux.gateway.asyncio.sleep", new=AsyncMock()):
            result = await gateway.send("g", "回复")
        self.assertFalse(result.confirmed)
        self.assertEqual(posts, 1)
        await gateway.close()

    async def test_model_402_is_not_retried(self):
        calls = 0

        async def handle(request):
            nonlocal calls
            calls += 1
            return httpx.Response(402, json={"error": "Insufficient balance"})

        engine = AIEngine("https://api.example.test/chat/completions", "test", max_retries=3)
        engine._clients[id(asyncio.get_running_loop())] = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        self.assertEqual(await engine.chat([{"role": "user", "content": "hi"}]), "")
        self.assertEqual(engine.last_error, "HTTP 402")
        self.assertEqual(calls, 1)
        await engine.close()


if __name__ == "__main__":
    unittest.main()
