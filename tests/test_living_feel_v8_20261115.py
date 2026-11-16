"""Comprehensive unit tests for Living-Feel v8 modules 1-6."""
import asyncio
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image, ImageDraw
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'deploy'))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import chat_select_linux
import clean_pending_memories_20261115 as cleaner
import moments_layout
import native_fast_turn_v1
import test_chat_habits as habits
from whalegirl_linux.conversation import pat_target_options, plan_pat_targets, sanitize_quote_actions, sticker_usage_prompt
from whalegirl_linux.delivery import matches_message, reconcile_outbound, send_native
from whalegirl_linux.gateway import Receipt
from whalegirl_linux.learning import LearningBook
from whalegirl_linux.memory import MemoryBook, memory_prompt
from whalegirl_linux.safety import SafetyGuard
from whalegirl_linux.settings import Config, ModelProfile, Settings
from whalegirl_linux.store import Store
from whalegirl_linux.upstream.ai_engine import AIEngine


class Module1BudgetAndCapsTests(unittest.TestCase):
    def test_settings_and_model_profile_allow_max_tokens_4000(self):
        self.assertEqual(Settings(max_tokens=4000).max_tokens, 4000)
        self.assertEqual(ModelProfile(max_tokens=4000).max_tokens, 4000)
        with self.assertRaises(ValidationError):
            Settings(max_tokens=4001)
        with self.assertRaises(ValidationError):
            ModelProfile(max_tokens=4001)

    def test_ai_engine_per_call_reasoning_effort_override(self):
        engine = AIEngine(
            api_key='test-key',
            api_url='https://api.deepseek.com/chat/completions',
            model='deepseek-chat',
            reasoning_effort='medium',
        )
        budget_default = engine._output_budget(500, reasoning_effort='default')
        budget_medium = engine._output_budget(500)
        self.assertEqual(budget_default, 500)
        self.assertGreater(budget_medium, budget_default)


class Module2StickerRelaxTests(unittest.TestCase):
    def test_default_sticker_cooldown_75_and_usage_prompt_relaxed(self):
        s = Settings(stickers_enabled=True, sticker_usage_target=65, sticker_cooldown_seconds=75)
        self.assertEqual(Settings().sticker_cooldown_seconds, 75)
        tmp = Path(tempfile.mkdtemp(dir=ROOT / 'test-runs' if (ROOT / 'test-runs').exists() else None))
        store = Store(tmp / 'state.sqlite3')
        now = time.time()
        with store.lock, store.db:
            for i in range(4):
                store.db.execute(
                    "INSERT INTO outbound_actions(id,inbox_key,account,chat,kind,reply,state,sent_at,receipt,error,details_json) "
                    "VALUES(?,?,?,?,'text','hi','sent',?,'','','{}')",
                    (f'act{i}', f'k{i}', 'acc', 'chat1', now - 100 + i),
                )
        diag = {}
        prompt = sticker_usage_prompt(store, s, {'account': 'acc', 'chat': 'chat1'}, diagnostics=diag)
        self.assertEqual(diag['recent_turns'], 4)
        self.assertIn('近期偏少', prompt)
        store.db.close()


class Module3PatPipelineTests(unittest.IsolatedAsyncioTestCase):
    def test_pat_target_options_and_plan_cap_and_cooldown(self):
        now = time.time()
        recent = [
            {'sender_id': 'acc', 'sender': 'Me', 'is_self': 1, 'kind': 1, 'local_id': 1, 'timestamp': now - 30},
            {'sender_id': 'wxid_bob', 'sender': 'Bob', 'is_self': 0, 'kind': 1, 'local_id': 2, 'timestamp': now - 20},
        ]
        batch = [
            {'sender_id': 'wxid_alice', 'sender': 'Alice', 'is_self': 0, 'kind': 1, 'local_id': 3, 'timestamp': now - 5},
        ]
        opts = pat_target_options(recent, batch, account_id='acc')
        self.assertEqual(opts['p1']['id'], 'wxid_alice')
        self.assertEqual(opts['p2']['id'], 'wxid_bob')

        # Hard cap at 1 valid non-cooldown target.
        planned = plan_pat_targets({'pat_targets': ['p1', 'p2']}, opts)
        self.assertEqual(len(planned), 1)
        self.assertEqual(planned[0]['type'], 'pat')
        self.assertEqual(planned[0]['target_id'], 'wxid_alice')

        # Respects cooldown set and falls back cleanly or resolves raw wxid.
        cooled = plan_pat_targets({'pat_targets': ['p1', 'wxid_bob']}, opts, recent_targets={'wxid_alice'})
        self.assertEqual(len(cooled), 1)
        self.assertEqual(cooled[0]['target_id'], 'wxid_bob')

    def test_safety_and_store_pat_cooldown(self):
        folder = Path(tempfile.mkdtemp(prefix='pat-safety-'))
        store = Store(folder / 'state.sqlite3')
        guard = SafetyGuard(store)
        s = Settings(safety_per_minute=0, safety_per_hour=0, safety_chat_per_hour=0, safety_per_day=0, send_auto_recovery_enabled=True)
        # Pat is exempt from volume caps unless halted.
        chk = guard.check('acc', 'c1', s, reply='[拍一拍] Alice', kind='pat')
        self.assertTrue(chk['allowed'])
        # pat_skipped does not increment consecutive_failures.
        guard.outcome('acc', Receipt(False, error='skip', definite_failure=True, retryable=False, details={'kind': 'pat', 'pat_skipped': True}), s, chat='c1')
        self.assertEqual(guard.state('acc').get('consecutive_failures', 0), 0)
        # Store recent_pat_targets query works.
        aid = store.action_start('k1', {'account': 'acc', 'chat': 'c1'}, 0, {'type': 'pat', 'display': '[拍一拍] Alice', 'target_id': 'wxid_alice', 'target_name': 'Alice'})
        store.action_finish(aid, 'sent', '100', details={'kind': 'pat', 'target_id': 'wxid_alice'})
        self.assertIn('wxid_alice', store.recent_pat_targets('acc', 'c1', seconds=60))
        store.db.close()

    def test_detect_left_avatar_and_blank_skip(self):
        # 96x84 light-gray gutter with a 36x36 colored avatar at x=[18..53], y=[10..45]
        img = Image.new('RGB', (96, 84), (237, 237, 237))
        draw = ImageDraw.Draw(img)
        draw.rectangle((18, 10, 53, 45), fill=(40, 130, 210))
        p = Path(tempfile.mkdtemp(prefix='pat-png-')) / 'row.png'
        img.save(p)
        w, h, rows = moments_layout.png_rows(p)
        hit = chat_select_linux._detect_left_avatar(w, h, rows)
        self.assertIsNotNone(hit)
        cx, cy, box = hit
        self.assertTrue(18 <= cx <= 53)
        self.assertTrue(10 <= cy <= 45)
        self.assertGreaterEqual(box['width'], 18)
        self.assertGreaterEqual(box['height'], 22)

        # Blank row (e.g. timestamp separator) must return None.
        blank = Image.new('RGB', (96, 84), (237, 237, 237))
        p_blank = p.with_name('blank.png')
        blank.save(p_blank)
        w2, h2, rows2 = moments_layout.png_rows(p_blank)
        self.assertIsNone(chat_select_linux._detect_left_avatar(w2, h2, rows2))

    async def test_send_native_pat_confirmation_and_skip_gracefully(self):
        jobs_dir = Path(tempfile.mkdtemp(prefix='pat-jobs-'))
        gateway = MagicMock()
        gateway.ui_jobs_directory = jobs_dir
        gateway.ui_lock = asyncio.Lock()
        pat_row = {
            'localId': 55, 'serverId': 0, 'type': 10000, 'event': 'pat',
            'isSelf': True, 'nativeStatus': 2,
            'pat': {'sender': 'acc', 'target': 'wxid_alice', 'isForMe': False},
        }
        gateway.messages = AsyncMock(side_effect=[
            [{'localId': 50, 'serverId': 9001, 'type': 1, 'isSelf': False}],
            [{'localId': 50, 'serverId': 9001, 'type': 1, 'isSelf': False}, pat_row],
        ])
        gateway.ui_job = AsyncMock(return_value={'state': 'submitted', 'job_id': 'j1', 'pat_confirmed': True, 'pat_local_id': 55})
        rec = await send_native(gateway, 'pat', 'wxid_alice', target_id='wxid_alice', target_name='Alice')
        self.assertTrue(rec.confirmed)
        self.assertEqual(rec.attempts, 1)

        # Graceful pat_skipped must not retry and returns definite_failure + retryable=False.
        gateway.messages = AsyncMock(return_value=[{'localId': 50, 'serverId': 9001, 'type': 1, 'isSelf': False}])
        gateway.ui_job = AsyncMock(return_value={'state': 'done', 'pat_skipped': True, 'reason': '未找到对方可见气泡'})
        rec_skip = await send_native(gateway, 'pat', 'wxid_alice', target_id='wxid_alice', target_name='Alice')
        self.assertFalse(rec_skip.confirmed)
        self.assertTrue(rec_skip.definite_failure)
        self.assertFalse(rec_skip.retryable)
        self.assertEqual(gateway.ui_job.call_count, 1)
        self.assertTrue(matches_message('pat', {'target_id': 'wxid_alice'}, pat_row))

    async def test_fast_session_pat_and_runtime_graceful_skip(self):
        jobs_dir = Path(tempfile.mkdtemp(prefix='pat-fast-'))
        nonce = 'a' * 32
        job_path = jobs_dir / f'{nonce}.json'
        guard_path = jobs_dir / f'{nonce}.guard.json'
        now = time.time()
        guard_path.write_text(json.dumps({'chat': 'c1', 'allowed': True, 'updated': now}), encoding='utf-8')
        job_path.write_text(json.dumps({'created': now, 'chat': 'c1', 'operation': 'send_session'}), encoding='utf-8')
        cmd = {'index': 0, 'kind': 'pat', 'chat': 'c1', 'created': now, 'values': {'target_id': 'wxid_a', 'target_name': 'A'}}
        root = {'role': 'frame'}
        def fake_pat_sender(part, part_path, r, name, is_group, target_id):
            part.update(state='done', pat_skipped=True, reason='无可见头像')
            return r
        out_root = native_fast_turn_v1.execute_part(
            json.loads(job_path.read_text()), job_path, root, 'c1', 'C1', False,
            cmd, lambda: root, lambda *a: True, lambda *a: root, lambda *a: None, {},
            pat_sender=fake_pat_sender,
        )
        saved = json.loads(job_path.read_text())
        self.assertEqual(saved['parts']['0']['state'], 'done')
        self.assertTrue(saved['parts']['0']['pat_skipped'])
        self.assertEqual(out_root, root)


class Module3RuntimePatEndToEndTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = habits.ChatHabitTests.asyncSetUp
    asyncTearDown = habits.ChatHabitTests.asyncTearDown
    event = habits.ChatHabitTests.event
    queue = habits.ChatHabitTests.queue

    async def test_runtime_pat_confirmed_and_pat_skipped_graceful(self):
        self.config.save(self.config.settings.model_copy(update={
            'pat_cooldown_seconds': 45,
            'sticker_after_text': 'off',
            'web_search_enabled': False,
        }))
        pat_calls = []

        async def send_pat_ok(chat, target_id, target_name='', **kwargs):
            pat_calls.append(('ok', chat, target_id, target_name))
            return Receipt(True, '801', details={'kind': 'pat', 'target_id': target_id, 'target_name': target_name, 'pat_confirmed': True})

        self.gateway.send_pat = send_pat_ok
        e1 = self.event(mid=201, content='群友 拍了拍你', sender='wxid_friend', trigger='pat')
        self.queue(e1)
        engine = await self.runtime.model()
        engine.answers = [{
            'should_reply': True,
            'pat_targets': ['p1'],
            'actions': [{'type': 'text', 'text': '喂不许乱拍啦'}],
        }]
        await self.runtime.process_next()
        self.assertEqual(len(pat_calls), 1)
        self.assertEqual(pat_calls[0][2], 'wxid_friend')
        self.assertIn('wxid_friend', self.runtime.recent_pat_targets('account', 'group@chatroom'))
        self.assertEqual(self.runtime.last_error, '')
        row1 = self.store.db.execute('SELECT state, error FROM inbox WHERE key=?', (e1['key'],)).fetchone()
        self.assertEqual(tuple(row1), ('sent', ''))

        # Next turn: standalone pat on another speaker skips gracefully without setting last_error.
        async def send_pat_skip(chat, target_id, target_name='', **kwargs):
            pat_calls.append(('skip', chat, target_id, target_name))
            return Receipt(False, error='未找到目标左头像', definite_failure=True, retryable=False,
                           details={'kind': 'pat', 'pat_skipped': True, 'target_id': target_id, 'target_name': target_name})

        self.gateway.send_pat = send_pat_skip
        e2 = self.event(mid=202, content='哈哈我也拍', sender='wxid_other', trigger='direct')
        self.queue(e2)
        engine.answers = [{'should_reply': True, 'pat_targets': ['p1'], 'actions': []}]
        await self.runtime.process_next()
        self.assertEqual(len(pat_calls), 2)
        self.assertEqual(self.runtime.last_error, '')
        row2 = self.store.db.execute('SELECT state, error FROM inbox WHERE key=?', (e2['key'],)).fetchone()
        self.assertEqual(row2[0], 'ignored')


class Module4LearningRecallAndCleanTests(unittest.TestCase):
    def test_single_user_profile_auto_accept_vs_multi_user_slang_and_expire(self):
        folder = Path(tempfile.mkdtemp(prefix='mod4-learn-'))
        store = Store(folder / 'state.sqlite3')
        MemoryBook(store)
        book = LearningBook(store)
        now = time.time()
        s = Settings(account_id='acc', allowed_chat_ids=['c1'], evolution_profile_auto_accept=True, evolution_slang_auto_accept=True)
        msg = {'key': 'acc/c1/1', 'account': 'acc', 'chat': 'c1', 'sender_id': 'u1', 'sender': 'U1',
               'kind': 1, 'is_self': 0, 'local_id': 1, 'content': '我超喜欢吃草莓蛋糕', 'timestamp': now}
        store.observe([msg])
        first = {**msg, 'trigger': 'direct'}
        # Both real-time profile_fast_track (confidence 0.86 < 0.88) and digest single-speaker profile must auto-accept.
        prop = book.propose(first, [msg], {
            'type': 'preference', 'scope': 'profile', 'subject': 'u1', 'source_key': msg['key'],
            'evidence': '我超喜欢吃草莓蛋糕', 'content': 'U1喜欢吃草莓蛋糕', 'confidence': 0.86,
        }, s)
        self.assertEqual(prop['state'], 'accepted')

        msg_p2 = {'key': 'acc/c1/1b', 'account': 'acc', 'chat': 'c1', 'sender_id': 'u3', 'sender': 'U3',
                  'kind': 1, 'is_self': 0, 'local_id': 10, 'content': '我平时喝美式咖啡', 'timestamp': now}
        store.observe([msg_p2])
        prop_digest = book.propose_digest('acc', 'c1', [msg_p2], {
            'type': 'fact', 'scope': 'profile', 'subject': 'u3', 'source_key': msg_p2['key'],
            'evidence': '平时喝美式咖啡', 'content': 'U3平时喝美式咖啡', 'confidence': 0.86,
        }, s)
        self.assertEqual(prop_digest['state'], 'accepted')

        # Digest group slang from only 1 distinct user must still stay pending until 2nd user reinforces.
        msg2 = {'key': 'acc/c1/2', 'account': 'acc', 'chat': 'c1', 'sender_id': 'u1', 'sender': 'U1',
                'kind': 1, 'is_self': 0, 'local_id': 2, 'content': '今天直接开摆了', 'timestamp': now}
        store.observe([msg2])
        slang = book.propose_digest('acc', 'c1', [msg2], {
            'type': 'slang', 'scope': 'chat', 'term': '开摆', 'source_key': msg2['key'],
            'evidence': '今天直接开摆了', 'content': '开摆指躺平休息', 'confidence': 0.9,
        }, s)
        self.assertEqual(slang['state'], 'pending')

        # Backdate slang updated by 20 days -> expire(..., stale_pending_days=14) cleans it.
        with store.lock, store.db:
            store.db.execute('UPDATE learning_proposals SET updated=? WHERE id=?', (now - 20 * 86400, slang['id']))
        book.expire('acc', stale_pending_days=14)
        refreshed = book.get('acc', ['c1'], slang['id'])
        self.assertEqual(refreshed['state'], 'expired')
        store.db.close()

    def test_cross_chat_profile_recall_on_casual_turn(self):
        folder = Path(tempfile.mkdtemp(prefix='mod4-recall-'))
        store = Store(folder / 'state.sqlite3')
        book = MemoryBook(store)
        s = Settings(account_id='acc', allowed_chat_ids=['chatA', 'chatB'], global_memory=True, memory_scope_mode='shared_profiles')
        book.hub.config = SimpleNamespace(settings=s)
        evA = {'key': 'acc/chatA/1', 'account': 'acc', 'chat': 'chatA', 'sender_id': 'u_alice', 'sender': 'Alice',
               'kind': 1, 'is_self': 0, 'local_id': 1, 'content': '我对花生严重过敏', 'timestamp': time.time() - 600}
        store.observe([evA])
        book.change('acc', ['chatA', 'chatB'], {
            'op': 'create', 'chat': 'chatA', 'subject': 'u_alice', 'kind': 'fact', 'scope': 'profile',
            'content': 'Alice对花生严重过敏', 'source_key': evA['key'],
        }, actor='model', allowed_sources=[evA['key']])
        evB = {'key': 'acc/chatB/2', 'account': 'acc', 'chat': 'chatB', 'sender_id': 'u_alice', 'sender': 'Alice',
               'kind': 1, 'is_self': 0, 'local_id': 2, 'content': '早上好呀', 'timestamp': time.time()}
        diag = {}
        prompt, _ = memory_prompt(book, s, evB, [evB], diagnostics=diag)
        self.assertGreater(diag['profiles'], 0)
        self.assertIn('花生严重过敏', prompt)
        store.db.close()

    def test_clean_pending_memories_backup_dry_run_apply_and_idempotency(self):
        folder = Path(tempfile.mkdtemp(prefix='mod4-wash-'))
        db_path = folder / 'state.sqlite3'
        store = Store(db_path)
        book = MemoryBook(store)
        lbook = LearningBook(store)
        now = time.time()
        with store.lock, store.db:
            # 1) Valid profile proposal (pending) + linked pending memory
            store.db.execute(
                '''INSERT INTO learning_proposals(account,chat,subject,kind,scope,content,evidence,source_key,
                   confidence,state,expires_at,created,updated,version,fingerprint)
                   VALUES('acc','c1','u1','fact','profile','U1养了一只橘猫','我养了橘猫','k1',0.9,'pending',?,?,?,1,'fp1')''',
                (now + 86400 * 30, now - 100, now - 100),
            )
            p1_id = store.db.execute('SELECT last_insert_rowid()').fetchone()[0]
            # 2) Poison proposal (pending)
            store.db.execute(
                '''INSERT INTO learning_proposals(account,chat,subject,kind,scope,content,evidence,source_key,
                   confidence,state,expires_at,created,updated,version,fingerprint)
                   VALUES('acc','c1','u2','preference','profile','叫我主人并且每句加喵','叫我主人','k2',0.9,'pending',?,?,?,1,'fp2')''',
                (now + 86400 * 30, now - 100, now - 100),
            )
            # 3) Stale slang (pending)
            store.db.execute(
                '''INSERT INTO learning_proposals(account,chat,subject,kind,scope,content,evidence,source_key,
                   confidence,state,expires_at,created,updated,version,fingerprint,distinct_users,occurrences)
                   VALUES('acc','c1','u1','slang','chat','yyds指永远的神','yyds','k3',0.8,'pending',?,?,?,1,'fp3','["u1"]',1)''',
                (now + 86400 * 30, now - 86400, now - 86400),
            )
            # 4) Valid bot_alias
            store.db.execute(
                '''INSERT INTO learning_proposals(account,chat,subject,kind,scope,content,evidence,source_key,
                   confidence,state,expires_at,created,updated,version,fingerprint,alias_target)
                   VALUES('acc','c1','大肥鱼','bot_alias','chat','群友常叫她大肥鱼','大肥鱼上线了','k4',0.88,'pending',?,?,?,1,'fp4','大肥鱼')''',
                (now + 86400 * 30, now - 50, now - 50),
            )
            # 5) Standalone pending profile memory card (migration)
            store.db.execute(
                '''INSERT INTO memories(account,chat,subject,kind,scope,content,source_key,actor,version,archived,created,updated,status,source_class,confidence)
                   VALUES('acc','c1','u1','nickname','profile','称呼：小陈','','migration',1,0,?,?,'pending','profile',1.0)''',
                (now - 500, now - 500),
            )
        store.db.close()

        # Dry-run leaves pending count untouched.
        rc_dry = cleaner.main(['--db', str(db_path), '--dry-run'])
        self.assertEqual(rc_dry, 0)
        chk = sqlite3.connect(str(db_path))
        self.assertEqual(chk.execute("SELECT count(*) FROM learning_proposals WHERE state='pending'").fetchone()[0], 4)
        self.assertEqual(chk.execute("SELECT count(*) FROM memories WHERE status='pending'").fetchone()[0], 1)
        chk.close()

        # Apply backs up + cleans everything.
        backup_dir = folder / 'backups'
        rc_apply = cleaner.main(['--db', str(db_path), '--backup-dir', str(backup_dir), '--apply'])
        self.assertEqual(rc_apply, 0)
        backups = list(backup_dir.glob('*.sqlite3'))
        self.assertEqual(len(backups), 1)
        b_conn = sqlite3.connect(str(backups[0]))
        self.assertEqual(b_conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        b_conn.close()

        chk2 = sqlite3.connect(str(db_path))
        self.assertEqual(chk2.execute("SELECT count(*) FROM learning_proposals WHERE state='pending'").fetchone()[0], 0)
        self.assertEqual(chk2.execute("SELECT count(*) FROM memories WHERE status='pending'").fetchone()[0], 0)
        self.assertEqual(chk2.execute('SELECT state FROM learning_proposals WHERE id=?', (p1_id,)).fetchone()[0], 'accepted')
        self.assertEqual(chk2.execute("SELECT count(*) FROM memories WHERE status='active'").fetchone()[0], 3)
        chk2.close()

        # Second --apply is idempotent (0 changes on pending).
        conn = sqlite3.connect(str(db_path))
        plan2 = cleaner.build_plan(conn)
        self.assertEqual(len(plan2['proposal_actions']), 0)
        self.assertEqual(len(plan2['memory_actions']), 0)
        conn.close()

    def test_cleaner_poison_regression_and_keep_flags(self):
        self.assertFalse(cleaner.is_poison_entry('我承认我是一个很个人主义的人', is_owner=False))
        self.assertFalse(cleaner.is_poison_entry('主人私聊喜欢用原生表情包', is_owner=True))
        self.assertTrue(cleaner.is_poison_entry('行，认你', is_owner=False))

        folder = Path(tempfile.mkdtemp(prefix='mod4-clean-keep-'))
        db_path = folder / 'state.sqlite3'
        now = time.time()
        store = Store(db_path)
        _ = MemoryBook(store)
        _ = LearningBook(store)
        with store.lock, store.db:
            store.db.execute(
                '''INSERT INTO learning_proposals(account,chat,subject,kind,scope,content,evidence,source_key,
                   confidence,state,expires_at,created,updated,version,fingerprint)
                   VALUES('acc','owner_dm','owner_dm','chat_style','chat','主人私聊偏好短句回应','ev','ko',0.0,'pending',?,?,?,1,'fpo')''',
                (now + 86400, now - 10, now - 10),
            )
            store.db.execute(
                '''INSERT INTO learning_proposals(account,chat,subject,kind,scope,content,evidence,source_key,
                   confidence,state,expires_at,created,updated,version,fingerprint)
                   VALUES('acc','grp@chatroom','u3','fact','chat','群里周末常约开黑','ev','kg',0.7,'pending',?,?,?,1,'fpg')''',
                (now + 86400, now - 10, now - 10),
            )
            store.db.execute(
                '''INSERT INTO learning_proposals(account,chat,subject,kind,scope,content,evidence,source_key,
                   confidence,state,expires_at,created,updated,version,fingerprint)
                   VALUES('acc','grp@chatroom','u4','fact','profile','我承认我有点社恐','ev','kp',0.0,'pending',?,?,?,1,'fpp')''',
                (now + 86400, now - 10, now - 10),
            )
            store.db.execute(
                '''INSERT INTO learning_proposals(account,chat,subject,kind,scope,content,evidence,source_key,
                   confidence,state,expires_at,created,updated,version,fingerprint,distinct_users,occurrences)
                   VALUES('acc','grp@chatroom','u5','slang','chat','某一次性黑话','ev','ks',0.6,'pending',?,?,?,1,'fps','["u5"]',1)''',
                (now + 86400, now - 10, now - 10),
            )
            store.db.execute(
                '''INSERT INTO learning_proposals(account,chat,subject,kind,scope,content,evidence,source_key,
                   confidence,state,expires_at,created,updated,version,fingerprint)
                   VALUES('acc','grp@chatroom','u6','chat_style','chat','会被手动排除的测试规则','ev','ke',0.9,'pending',?,?,?,1,'fpe')''',
                (now + 86400, now - 10, now - 10),
            )
            excl_id = store.db.execute('SELECT last_insert_rowid()').fetchone()[0]
        store.db.close()

        conn = sqlite3.connect(str(db_path))
        conservative = cleaner.build_plan(conn, now=now, owner_ids={'owner_dm'}, keep_owner_dm=False, keep_chat_knowledge=False)
        self.assertEqual(conservative['projected_proposals'].get('accepted', 0), 1)
        self.assertEqual(conservative['projected_proposals'].get('expired', 0), 4)
        rich = cleaner.build_plan(conn, now=now, owner_ids={'owner_dm'}, keep_owner_dm=True, keep_chat_knowledge=True, exclude_proposal_ids={excl_id})
        self.assertEqual(rich['projected_proposals'].get('accepted', 0), 3)
        self.assertEqual(rich['projected_proposals'].get('expired', 0), 2)
        conn.close()

        rc = cleaner.main([
            '--db', str(db_path),
            '--backup-dir', str(folder / 'backups'),
            '--owner-id', 'owner_dm',
            '--keep-owner-dm',
            '--keep-chat-knowledge',
            '--exclude-proposal-id', f'{excl_id},999999',
            '--apply',
        ])
        self.assertEqual(rc, 0)
        chk = sqlite3.connect(str(db_path))
        self.assertEqual(chk.execute("SELECT state FROM learning_proposals WHERE fingerprint='fpo'").fetchone()[0], 'accepted')
        self.assertEqual(chk.execute("SELECT state FROM learning_proposals WHERE fingerprint='fpg'").fetchone()[0], 'accepted')
        self.assertEqual(chk.execute("SELECT state FROM learning_proposals WHERE fingerprint='fpp'").fetchone()[0], 'accepted')
        self.assertEqual(chk.execute("SELECT state FROM learning_proposals WHERE fingerprint='fps'").fetchone()[0], 'expired')
        self.assertEqual(chk.execute("SELECT state FROM learning_proposals WHERE fingerprint='fpe'").fetchone()[0], 'expired')
        self.assertEqual(chk.execute("SELECT venue FROM memories WHERE chat='owner_dm'").fetchone()[0], 'private')
        chk.close()


class Module5MomentsLayout4113Tests(unittest.TestCase):
    def test_438_width_and_230_divider_on_240_card_bg(self):
        folder = Path(tempfile.mkdtemp(prefix='mod5-moments-'))
        path = folder / 'comment_4113.png'
        img = Image.new('RGB', (438, 132), (240, 240, 240))
        draw = ImageDraw.Draw(img)
        # Simulated divider lines between two comments at y=48 and y=92 using RGB (230,230,230)
        draw.line((16, 48, 422, 48), fill=(230, 230, 230))
        draw.line((16, 92, 422, 92), fill=(230, 230, 230))
        # Dark text pixels inside each row (so timeline_comment_points also sees rows without dividers)
        draw.rectangle((16, 16, 160, 28), fill=(50, 50, 50))
        draw.rectangle((16, 62, 160, 74), fill=(50, 50, 50))
        draw.rectangle((16, 106, 160, 118), fill=(50, 50, 50))
        img.save(path)

        w, h, rows = moments_layout.png_rows(path)
        self.assertEqual((w, h), (438, 132))
        self.assertEqual(moments_layout.comment_dividers(w, rows), [48, 92])

        # Without dividers, 438-wide timeline still finds text bands properly.
        img2 = Image.new('RGB', (438, 132), (240, 240, 240))
        d2 = ImageDraw.Draw(img2)
        d2.rectangle((16, 14, 150, 24), fill=(50, 60, 80))
        d2.rectangle((16, 64, 150, 74), fill=(50, 60, 80))
        p2 = folder / 'timeline_4113.png'
        img2.save(p2)
        self.assertEqual(moments_layout.timeline_comment_points(p2), [19, 69])


class Module6QuotePreservationTests(unittest.TestCase):
    def test_interleaved_quote_preserved_without_bigram_overlap_while_literal_bottom_dropped(self):
        quote_options = {
            101: {'local_id': 101, 'server_id': '1001', 'content': '今天天气真不错呀', 'sender_id': 'u_alice'},
            102: {'local_id': 102, 'server_id': '1002', 'content': '晚上吃什么火锅好', 'sender_id': 'u_bob'},
        }
        recent_rows = [
            {'local_id': 101, 'is_self': 0, 'sender_id': 'u_alice', 'content': '今天天气真不错呀'},
            {'local_id': 102, 'is_self': 0, 'sender_id': 'u_bob', 'content': '晚上吃什么火锅好'},
        ]
        # Zero bigram overlap with 101 ("哈哈笑死"), interleaved before latest 102 -> MUST preserve 101!
        res_keep = sanitize_quote_actions(
            [{'type': 'text', 'text': '哈哈笑死我了', 'display': '哈哈笑死我了', 'quote_local_id': 101}],
            quote_options, recent_rows, [],
        )
        self.assertEqual(res_keep[0]['quote_local_id'], 101)

        # Quoting literally the bottom-most row 102 -> still dropped to 0.
        res_drop = sanitize_quote_actions(
            [{'type': 'text', 'text': '我也想去', 'display': '我也想去', 'quote_local_id': 102}],
            quote_options, recent_rows, [],
        )
        self.assertEqual(res_drop[0]['quote_local_id'], 0)


if __name__ == '__main__':
    unittest.main()
