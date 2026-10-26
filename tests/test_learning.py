"""Learning boundaries, administrator lifecycle and normal-chat call budget."""
import json
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient
from test_chat_habits import Engine, Gateway
from whalegirl_linux.app import create_app
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store

ROOT = Path(__file__).resolve().parents[1]


def directory():
    runs = ROOT/'test-runs'
    runs.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix='learning-', dir=runs))


def event(mid=1, chat='a@chatroom', sender='person', content='我喜欢猫和安静聊天', **extra):
    return dict(key=f'account/{chat}/{mid}', account='account', chat=chat, sender=sender,
                sender_id=sender, kind=1, is_self=0, local_id=mid, content=content,
                timestamp=time.time(), trigger='direct', payload_json='{}', **extra)


def proposal(source, **extra):
    return dict(type='preference', scope='profile', subject=source['sender_id'],
                source_key=source['key'], evidence='我喜欢猫', content='喜欢猫',
                confidence=.95, ttl_days=30, **extra)


class RecordingEngine(Engine):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.prompts = []

    async def chat(self, messages, **kwargs):
        self.prompts.append(kwargs['system_prompt'])
        return await super().chat(messages, **kwargs)


class LearningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = Config(directory())
        self.config.save(Settings(enabled=True, account_id='account',
            allowed_chat_ids=['a@chatroom', 'b@chatroom'], global_memory=True,
            memory_scope_mode='shared_profiles', stickers_enabled=False, images_chat_enabled=False,
            debounce_single_seconds=0, debounce_burst_seconds=0, debounce_max_seconds=0), 'test-key')
        self.store = Store(self.config.directory/'state.sqlite3')
        self.runtime = Runtime(self.config, self.store, Gateway(), ROOT/'styles', engine_factory=RecordingEngine)
        self.book = self.runtime.learning.book

    async def asyncTearDown(self):
        await self.runtime.close()

    def add(self, source, **changes):
        self.store.observe([source])
        value = {**proposal(source), **changes}
        return self.book.propose(source, [source], value, self.config.settings)

    async def test_review_edit_revoke_expire_and_duplicate_cannot_reactivate(self):
        source = event()
        row = self.add(source)
        self.assertEqual(row['state'], 'accepted')
        row = self.book.change('account', self.config.settings.allowed_chat_ids, row['id'],
                               {'op': 'revise', 'version': 1, 'content': '更喜欢安静聊天', 'ttl_days': 1})
        self.assertEqual((row['state'], row['version']), ('pending', 2))
        with self.assertRaises(ValueError):
            self.book.change('account', self.config.settings.allowed_chat_ids, row['id'], {'op': 'accept', 'version': 1})
        row = self.book.change('account', self.config.settings.allowed_chat_ids, row['id'], {'op': 'accept', 'version': 2})
        row = self.book.change('account', self.config.settings.allowed_chat_ids, row['id'], {'op': 'revoke', 'version': 3})
        self.assertEqual(self.add(source)['state'], 'revoked')
        detail = self.book.detail('account', self.config.settings.allowed_chat_ids, row['id'])
        self.assertEqual(len(detail['revisions']), 4)
        self.assertEqual(detail['revisions'][0]['before']['state'], 'accepted')
        pending = self.add(source, type='tool_learning', scope='chat', content='工具失败时不能假装发送成功')
        self.assertEqual(pending['state'], 'pending')
        pending = self.book.change('account', self.config.settings.allowed_chat_ids, pending['id'],
                                   {'op': 'reject', 'version': 1})
        self.assertEqual(pending['state'], 'rejected')
        expired = self.add(source, content='喜欢猫但不喜欢吵闹')
        with self.store.lock, self.store.db:
            self.store.db.execute('UPDATE learning_proposals SET expires_at=? WHERE id=?', (time.time()-1, expired['id']))
        self.assertEqual(self.book.recall(source, [source], '猫', self.config.settings), [])
        self.book.expire('account')
        self.assertEqual(self.add(source, content='喜欢猫但不喜欢吵闹')['state'], 'expired')

    async def test_evidence_is_from_real_current_author_not_quotes_or_forwards(self):
        source = event(content='[当前发言] 我不养宠物\n[引用原话｜朋友] 我喜欢猫')
        source['payload_json'] = json.dumps({'reply': {'serverId': '123'}})
        self.store.observe([source])
        for changes in ({}, {'subject': 'other'}, {'source_key': 'not-received'}):
            with self.assertRaises(ValueError):
                self.book.propose(source, [source], {**proposal(source), **changes}, self.config.settings)
        source = event(2)
        source['payload_json'] = json.dumps({'forwarded': {'title': '聊天记录'}})
        with self.assertRaises(ValueError):
            self.add(source)
        source = event(3)
        self.store.observe([source])
        with self.assertRaises(ValueError):
            self.book.propose(source, [], proposal(source), self.config.settings)

    async def test_group_isolation_and_personal_recall_respect_sharing_switch(self):
        source = event()
        profile = self.add(source)
        group = self.add(source, scope='chat', type='chat_style', content='在这个群聊天简短一些')
        other_person = self.add(event(2, sender='other'), content='另一个人喜欢猫')
        incoming = event(3, chat='b@chatroom')
        self.store.observe([incoming])
        rows = self.book.recall(incoming, [incoming], '猫', self.config.settings)
        self.assertEqual([r['id'] for r in rows], [profile['id']])
        local = self.book.recall(source, [source], '猫', self.config.settings)
        self.assertIn(group['id'], [r['id'] for r in local])
        self.assertNotIn(other_person['id'], [r['id'] for r in local])
        self.config.settings.global_memory = False
        self.assertEqual(self.book.recall(incoming, [incoming], '猫', self.config.settings), [])
        self.assertIn(profile['id'], [r['id'] for r in self.book.recall(source, [source], '猫', self.config.settings)])
        self.assertEqual(self.book.recall({**source, 'account': 'other'}, [source], '猫', self.config.settings), [])

    async def test_disabled_and_zero_eagerness_stop_proposals_but_zero_keeps_recall(self):
        source = event()
        self.add(source)
        service = self.runtime.learning
        self.config.settings.learning_eagerness = 0
        context, recalled = service.context(source, [source], '猫')
        self.assertIn('喜欢猫', context)
        service.offer(source, [source], [{**proposal(source), 'content': '不能新建'}], recalled)
        await service.process_pending()
        self.assertEqual(len(service.snapshot()['proposals']), 1)
        self.assertEqual(service.snapshot()['proposals'][0]['recall_count'], 1)
        self.config.settings.learning_enabled = False
        self.assertEqual(service.context(source, [source], '猫'), ('', []))
        self.assertEqual(service.static_prompt(), '')
        service.offer(source, [source], [proposal(source)])
        self.assertTrue(service.queue.empty())

    async def test_normal_reply_uses_one_call_and_preserves_static_cache_prefix(self):
        source = event()
        self.store.insert(source)
        self.store.observe([source])
        engine = await self.runtime.model()
        engine.answers = [{'should_reply': True, 'actions': [{'type': 'text', 'text': '猫猫很可爱呀'}],
                           'learning_proposals': [proposal(source)]}]
        await self.runtime.process_next()
        self.assertEqual(engine.calls, 1)
        self.assertEqual(self.runtime.gateway.sent, [('text', '猫猫很可爱呀')])
        self.assertEqual(self.book.snapshot('account', self.config.settings.allowed_chat_ids)['proposals'], [])
        await self.runtime.learning.process_pending()
        self.assertEqual(self.runtime.learning.snapshot()['proposals'][0]['state'], 'accepted')
        engine.answers = [{'should_reply': False, 'actions': []}]
        second = event(2, content='鲸鱼娘 猫猫可爱吗')
        self.store.insert(second)
        self.store.observe([second])
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE inbox SET updated=updated-300 WHERE state='sent'")
            self.store.db.execute("UPDATE outbound_actions SET sent_at=sent_at-300")
        await self.runtime.process_next()
        self.assertEqual(engine.calls, 2)
        self.assertEqual(engine.prompts[0].split('【本轮动态上下文】')[0],
                         engine.prompts[1].split('【本轮动态上下文】')[0])
        self.assertIn('喜欢猫', engine.prompts[1].split('【本轮动态上下文】')[1])


class LearningApiTests(unittest.TestCase):
    def test_authenticated_review_and_revision_api(self):
        app = create_app(directory(), gateway=Gateway(), engine_factory=Engine, run_background=False)
        config = app.state.config
        config.save(Settings(account_id='account', allowed_chat_ids=['a@chatroom'], learning_auto_accept=False))
        runtime, source = app.state.runtime, event()
        runtime.store.observe([source])
        row = runtime.learning.book.propose(source, [source], proposal(source), config.settings)
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/learning').status_code, 401)
            h = {'Authorization': 'Bearer '+config.admin_token}
            self.assertEqual(client.get('/learning.js').status_code, 200)
            self.assertEqual(client.get('/api/learning?state=invalid', headers=h).status_code, 400)
            self.assertEqual(client.get('/api/learning?chat=unauthorized', headers=h).status_code, 400)
            result = client.get('/api/learning?state=pending', headers=h).json()
            self.assertEqual(result['counts']['pending'], 1)
            path = '/api/learning/'+str(row['id'])
            self.assertEqual(client.post(path, headers=h, json={'op': 'accept', 'version': 1}).status_code, 200)
            self.assertEqual(client.post(path, headers=h, json={'op': 'revoke', 'version': 1}).status_code, 409)
            self.assertEqual(client.post(path, headers=h, json={'op': 'revoke', 'version': 2}).status_code, 200)
            self.assertEqual(len(client.get(path, headers=h).json()['revisions']), 3)
            self.assertEqual(client.get('/api/learning/999', headers=h).status_code, 404)
