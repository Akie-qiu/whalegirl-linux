"""Opt-in conversational agency, grounded follow-ups and incremental learning.

The switch gates every new behavior. No timer calls a model without new evidence
or a due intention, and every outbound action still uses the existing ledger.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
import uuid

from .memory import own_text
from .experience import ExperienceService
from .topic_relevance import current_query, related_topic


def enabled(settings):
    return bool(getattr(settings, 'autonomy_enabled', False))


STATIC_PROMPT = '''
【自主参与】保持真实少女活人感。看清语境自由选择旁观、文字、原生表情或穿插发送，不凑格式或字数；普通任务及时做。
可选JSON扩展字段（无变动留空）：
1. participation_reason：简短行动依据（供管理员查看）。
2. intent_ops：最多2项后续跟进，基于真实来由：
   - 规划：{"op":"plan","topic":"以后想接续的具体事情","source_key":"本会话真实消息key","after_minutes":60,"expires_minutes":1440}（到期须大于等待，最长10080分）
   - 结束：{"op":"complete|cancel","id":"确切意图ID"}。普通寒暄不建，不承诺一定到点联系。
3. mood_update：真实互动引起的情绪微澜：
   {"mood":"normal|proud|flustered|grumpy|jealous|hyped|sleepy","energy_delta":2,"source_key":"真实消息key","reason":"具体互动依据"}。无新情绪事件留空自然平复。
4. learning_proposals：相处经验写清适用场景与例外，引用真实发言。
'''


class AutonomyService:
    def __init__(self, runtime):
        self.runtime, self.store = runtime, runtime.store
        self.busy = False
        self.last_error = ''
        self._mode = None
        self._next_growth = 0.0
        self.deferred_until = {}
        self.experience = ExperienceService(runtime)
        with self.store.lock, self.store.db:
            self.store.db.executescript('''
                CREATE TABLE IF NOT EXISTS autonomy_chats(
                    account TEXT,chat TEXT,cursor INTEGER NOT NULL DEFAULT 0,
                    last_considered REAL NOT NULL DEFAULT 0,last_anchor TEXT NOT NULL DEFAULT '',
                    growth_at REAL NOT NULL DEFAULT 0,PRIMARY KEY(account,chat));
                CREATE TABLE IF NOT EXISTS autonomy_intents(
                    id TEXT PRIMARY KEY,account TEXT NOT NULL,chat TEXT NOT NULL,
                    source_key TEXT NOT NULL,topic TEXT NOT NULL,fingerprint TEXT NOT NULL,
                    due REAL NOT NULL,expires REAL NOT NULL,state TEXT NOT NULL DEFAULT 'pending',
                    created REAL NOT NULL,updated REAL NOT NULL,
                    UNIQUE(account,chat,fingerprint));
                CREATE INDEX IF NOT EXISTS autonomy_intents_due
                    ON autonomy_intents(account,chat,state,due);
                CREATE TABLE IF NOT EXISTS autonomy_events(
                    id INTEGER PRIMARY KEY,account TEXT NOT NULL,chat TEXT NOT NULL,
                    source_key TEXT NOT NULL,kind TEXT NOT NULL,reason TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS autonomy_events_scope
                    ON autonomy_events(account,chat,created);
            ''')

        with self.store.lock, self.store.db:
            # Additive migration: keep old IDs, deadlines and cancelled intentions.
            for table, extras in (
                ('autonomy_intents', {'role_id': "TEXT NOT NULL DEFAULT ''", 'kind': "TEXT NOT NULL DEFAULT 'followup'"}),
                ('autonomy_events', {'role_id': "TEXT NOT NULL DEFAULT ''"}),
                ('autonomy_chats', {'role_id': "TEXT NOT NULL DEFAULT ''"}),
            ):
                columns = {r[1] for r in self.store.db.execute('PRAGMA table_info(' + table + ')')}
                for name, definition in extras.items():
                    if name not in columns:
                        self.store.db.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + name + ' ' + definition)
                        if name == 'role_id':
                            self.store.db.execute('UPDATE ' + table + ' SET role_id=?', (self.role(),))
            self.store.db.execute('''CREATE INDEX IF NOT EXISTS autonomy_events_reading
                ON autonomy_events(account,chat,role_id,kind,source_key)''')

    def role(self):
        return self.experience.hub.role()

    def active(self):
        return enabled(self.runtime.config.settings)

    def static_prompt(self):
        return STATIC_PROMPT + self.experience.static_prompt() if self.active() else ''

    def sync_mode(self):
        s = self.runtime.config.settings
        mode = (s.account_id, enabled(s), tuple(s.allowed_chat_ids), self.role())
        if self._mode == mode:
            return
        if not mode[1] or not s.account_id:
            self._mode = mode
            return
        with self.store.lock, self.store.db:
            for chat in s.allowed_chat_ids:
                maximum = self.store.db.execute(
                    'SELECT COALESCE(max(rowid),0) FROM message_log WHERE account=? AND chat=?',
                    (s.account_id, chat)).fetchone()[0]
                self.store.db.execute('INSERT OR IGNORE INTO autonomy_chats(account,chat,cursor,role_id) VALUES(?,?,?,?)',
                                      (s.account_id, chat, maximum, self.role()))
                # Re-enabling starts from current observations, not an old backlog.
                if self._mode is None or self._mode[:2] != mode[:2]:
                    self.store.db.execute('UPDATE autonomy_chats SET cursor=? WHERE account=? AND chat=?',
                                          (maximum, s.account_id, chat))
                old = self.store.db.execute('SELECT role_id FROM autonomy_chats WHERE account=? AND chat=?',
                                            (s.account_id, chat)).fetchone()
                if old['role_id'] != self.role():
                    self.store.db.execute('''UPDATE autonomy_chats SET role_id=?,cursor=?,last_anchor='',
                        last_considered=0,growth_at=0 WHERE account=? AND chat=?''',
                        (self.role(), maximum, s.account_id, chat))
        self._mode = mode

    def log(self, first, kind, reason='', **details):
        if not self.active():
            return
        with self.store.lock, self.store.db:
            self.store.db.execute('''INSERT INTO autonomy_events
                (account,chat,source_key,kind,reason,details_json,created,role_id) VALUES(?,?,?,?,?,?,?,?)''',
                (first['account'], first['chat'], first['key'], kind, str(reason)[:180],
                 json.dumps(details, ensure_ascii=False), time.time(), self.role()))

    def admit(self, first, batch, social):
        """Timing controls request frequency; semantics stay with the main model."""
        if not self.active():
            return True
        s = self.runtime.config.settings
        now = time.time()
        natural = all(row.get('trigger') == 'natural' for row in batch)
        state = self.store.db.execute('SELECT last_considered FROM autonomy_chats WHERE account=? AND chat=?',
                                      (first['account'], first['chat'])).fetchone()
        gap = {'quiet': 12, 'balanced': 5, 'active': 2}[s.participation]
        if natural and state and now - state[0] < gap:
            self.deferred_until[first['chat']] = state[0] + gap
            self.log(first, 'observe', '新消息已保留，合并到后续会话判断')
            return False
        with self.store.lock, self.store.db:
            self.store.db.execute('INSERT OR IGNORE INTO autonomy_chats(account,chat) VALUES(?,?)',
                                  (first['account'], first['chat']))
            self.store.db.execute('UPDATE autonomy_chats SET last_considered=? WHERE account=? AND chat=?',
                                  (now, first['account'], first['chat']))
        social['decision_mode'] = 'autonomous'
        return True

    def waiting_chats(self):
        if not self.active():
            self.deferred_until.clear()
            return set()
        now = time.time()
        self.deferred_until = {chat: until for chat, until in self.deferred_until.items() if until > now}
        return set(self.deferred_until)

    def pending(self, account, chat, *, due=False, now=None):
        now = time.time() if now is None else now
        if (not self.active() or account != self.runtime.config.settings.account_id
                or chat not in self.runtime.config.settings.allowed_chat_ids):
            return []
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE autonomy_intents SET state='expired',updated=? WHERE account=? AND chat=? AND role_id=? AND state='pending' AND expires<=?",
                                  (now, account, chat, self.role(), now))
            return [dict(r) for r in self.store.db.execute(
                "SELECT * FROM autonomy_intents WHERE account=? AND chat=? AND role_id=? AND state='pending' AND expires>?"
                + (" AND kind='followup' AND due<=?" if due else '') + ' ORDER BY due,created LIMIT 12',
                (account, chat, self.role(), now, now) if due else (account, chat, self.role(), now))]

    def dynamic_prompt(self, first, batch=None, *, proactive=False):
        if not self.active():
            return ''
        batch = batch if batch is not None else [first]
        text = self.experience.social_prompt(first, batch)
        text += self.experience.emotional_context(first)
        text += self.experience.interaction_prompt(first)
        if proactive:
            text += self.experience.reading_prompt(first)
        query = current_query(batch)
        rows = self.pending(first['account'], first['chat'])
        rows = [r for r in rows if (r['kind'] != 'unsaid' or self.experience.active()) and (
                (proactive and r['kind'] == 'followup' and r['due'] <= time.time())
                or related_topic(query, r['topic'] + ' ' + own_text(self.experience.hub.source(first['account'], r['source_key']) or {})))]
        if not rows:
            return text
        brief = [{k:r[k] for k in ('id', 'kind', 'topic', 'source_key', 'due', 'expires')} for r in rows[:2]]
        for item in brief:
            source = self.store.db.execute('SELECT * FROM message_log WHERE account=? AND chat=? AND key=?',
                (first['account'], first['chat'], item['source_key'])).fetchone()
            if source:
                item['source'] = {'sender_id': source['sender_id'], 'sender': source['sender'],
                                  'timestamp': source['timestamp'], 'content': own_text(dict(source))[:140]}
        return text + '\n本会话相关的待接续想法（可取消，不是当前新消息或必做任务）：' + json.dumps(brief, ensure_ascii=False)

    def opportunity(self, chat, *, anchor='', now=None):
        if not self.active():
            return True
        s = self.runtime.config.settings
        if self.pending(s.account_id, chat, due=True, now=now):
            return True
        if self.experience.fresh_reading({'account':s.account_id, 'chat':chat}):
            return True
        row = self.store.db.execute('SELECT last_anchor FROM autonomy_chats WHERE account=? AND chat=?',
                                    (s.account_id, chat)).fetchone()
        return bool(anchor and (not row or row[0] != anchor))

    def considered(self, first, anchor):
        if not self.active():
            return
        with self.store.lock, self.store.db:
            self.store.db.execute('INSERT OR IGNORE INTO autonomy_chats(account,chat) VALUES(?,?)',
                                  (first['account'], first['chat']))
            self.store.db.execute('UPDATE autonomy_chats SET last_anchor=? WHERE account=? AND chat=?',
                                  (anchor, first['account'], first['chat']))

    def finish_due(self, first, due):
        """A considered intention is not polled again indefinitely."""
        if not self.active():
            return
        with self.store.lock, self.store.db:
            for row in due:
                self.store.db.execute("UPDATE autonomy_intents SET state='done',updated=? WHERE id=? AND account=? AND chat=? AND role_id=? AND kind='followup' AND state='pending'",
                                      (time.time(), row['id'], first['account'], first['chat'], self.role()))

    def accept(self, first, batch, decision, *, proactive=False):
        mood_applied = False
        if not self.active() or not isinstance(decision, dict):
            return mood_applied
        s = self.runtime.config.settings
        if first['account'] != s.account_id or first['chat'] not in s.allowed_chat_ids:
            return mood_applied
        sources = {r['key']: r for r in batch if r.get('is_self', 0) == 0 and r.get('sender_id')
                   and r['account'] == first['account'] and r['chat'] == first['chat']}
        sources = {key:source for key in sources
                   if (source := self.experience.hub.source(first['account'], key))
                   and source['chat'] == first['chat'] and not source['is_self'] and source['sender_id']}
        now = time.time()
        if not proactive and batch:
            self.considered(first, max(batch, key=lambda row:(row['timestamp'], row.get('local_id') or 0))['key'])
        ops = decision.get('intent_ops')
        for op in ops[:2] if isinstance(ops, list) else []:
            if not isinstance(op, dict):
                continue
            if op.get('op') in ('cancel', 'complete') and isinstance(op.get('id'), str):
                with self.store.lock, self.store.db:
                    self.store.db.execute("UPDATE autonomy_intents SET state=?,updated=? WHERE id=? AND account=? AND chat=? AND role_id=? AND state='pending'",
                        ('cancelled' if op['op'] == 'cancel' else 'done', now, op['id'], first['account'], first['chat'], self.role()))
            elif (op.get('op') == 'plan' and isinstance(op.get('source_key'), str)
                    and op['source_key'] in sources):
                topic = op.get('topic')
                intent_kind = op.get('kind', 'followup')
                if intent_kind not in ('followup', 'unsaid') or (intent_kind == 'unsaid' and not self.experience.active()):
                    continue
                after, expires = op.get('after_minutes'), op.get('expires_minutes', 1440)
                if not isinstance(topic, str) or not topic.strip() or len(topic) > 240:
                    continue
                if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (after, expires)):
                    continue
                if not (0 if intent_kind == 'unsaid' else 1) <= after < expires <= 10080:
                    continue
                # Same topic stays a single intention even if repeatedly proposed.
                fingerprint = hashlib.sha256((self.role() + '/' + intent_kind + '/' + ''.join(topic.casefold().split())).encode()).hexdigest()
                with self.store.lock, self.store.db:
                    # A replay must not renew a completed, cancelled or expired thought.
                    if self.store.db.execute('''SELECT 1 FROM autonomy_intents WHERE account=? AND chat=?
                        AND role_id=? AND source_key=? AND kind=?''',
                        (first['account'], first['chat'], self.role(), op['source_key'], intent_kind)).fetchone():
                        continue
                    if len(self.pending(first['account'], first['chat'])) >= 12:
                        continue
                    self.store.db.execute('''INSERT OR IGNORE INTO autonomy_intents
                        (id,account,chat,source_key,topic,fingerprint,due,expires,created,updated,role_id,kind)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (uuid.uuid4().hex, first['account'], first['chat'],
                        op['source_key'], topic.strip(), fingerprint, now+after*60, now+expires*60, now, now, self.role(), intent_kind))
        mood = decision.get('mood_update')
        if s.mood_engine_enabled and isinstance(mood, dict):
            src_key = mood.get('source_key') if isinstance(mood.get('source_key'), str) and mood.get('source_key') else (batch[-1]['key'] if batch else '')
            seen = self.store.db.execute('''SELECT 1 FROM autonomy_events WHERE account=? AND chat=?
                AND role_id=? AND source_key=? AND kind='mood_changed' LIMIT 1''',
                (first['account'], first['chat'], self.role(), src_key)).fetchone()
            if src_key in sources and not seen:
                from .conversation import MOOD_LABELS, current_mood_state
                delta = mood.get('energy_delta', 0)
                if (mood.get('mood') in MOOD_LABELS and not isinstance(delta, bool)
                        and isinstance(delta, (int, float)) and math.isfinite(delta)):
                    current = current_mood_state(self.store, s, first['account'], first['chat'])
                    energy = max(10, min(100, current['energy'] + round(max(-18, min(18, delta)))))
                    with self.store.lock, self.store.db:
                        self.store.db.execute('''INSERT INTO persona_mood_state(account,chat,mood,energy,reason,life_override,updated)
                            VALUES(?,?,?,?,?,?,?) ON CONFLICT(account,chat) DO UPDATE SET
                            mood=excluded.mood,energy=excluded.energy,reason=excluded.reason,updated=excluded.updated''',
                            (first['account'], first['chat'], mood['mood'], energy,
                             str(mood.get('reason') or '根据实际互动调整')[:120], current.get('life_override', ''), now))
                    mood_applied = True
                    self.log({**first, 'key':src_key}, 'mood_changed', '根据本轮真实互动更新一次情绪',
                             mood=mood['mood'], state_updated=now)
        self.experience.capture(first, batch, decision.get('experience_update'))
        if decision.get('should_reply') is False:
            self.experience.settle(first, 'silent')
        if proactive:
            self.experience.consume_reading(first)
        kind = 'speak' if decision.get('should_reply') else 'silent'
        self.log(first, kind, decision.get('participation_reason') or decision.get('reason') or 'AI 根据当前会话选择行动',
                 mode='proactive' if proactive else 'live',
                 actions=[a.get('type') for a in decision.get('actions', [])[:6] if isinstance(a, dict)]
                 if isinstance(decision.get('actions'), list) else [])
        return mood_applied

    async def grow_once(self):
        r, s = self.runtime, self.runtime.config.settings
        if (not self.active() or not s.enabled or not s.learning_enabled or not s.learning_eagerness
                or not s.memory_enabled or not s.memory_consolidation_enabled or r.processing
                or r.store.queue() or r.proactive.busy or r.paused_reason or self.busy
                or r.account_id != s.account_id or r.gateway_state.get('status') != 'logged_in'):
            return False
        self.sync_mode()
        now = time.time()
        if now < self._next_growth:
            return False
        minimum = 5 if s.learning_eagerness >= 80 else 10
        selected = None
        for chat in s.allowed_chat_ids:
            state = self.store.db.execute('SELECT cursor,growth_at FROM autonomy_chats WHERE account=? AND chat=?',
                                          (s.account_id, chat)).fetchone()
            if not state or now - state['growth_at'] < 900:
                continue
            rows = [dict(x) for x in self.store.db.execute('''SELECT rowid AS observation_id,* FROM message_log
                WHERE account=? AND chat=? AND rowid>? AND is_self=0 AND sender_id!=''
                ORDER BY rowid LIMIT 60''',
                (s.account_id, chat, state['cursor']))]
            humans = [v for v in rows if v['is_self'] == 0 and v['sender_id'] and v['content'].strip()
                      and v['timestamp'] >= now - 86400]
            if len(humans) >= minimum:
                selected = chat, rows, humans
                break
            if rows and not humans:
                self._growth_cursor(chat, rows[-1]['observation_id'], now)
        if not selected:
            self._next_growth = now + 30
            return False
        chat, rows, humans = selected
        generation = r.config.generation
        self.busy = True
        self._next_growth = now + 300
        engine = None
        try:
            from .learning import STATIC_PROMPT as LEARNING_PROMPT
            engine = r.engine_factory(api_url=s.api_url, api_key=r.config.api_key(), api_style=s.api_style,
                reasoning_effort=s.reasoning_effort, stream_enabled=s.model_stream_enabled,
                model=s.model, max_tokens=750 if self.experience.active() else 500, temperature=.35, timeout=12, max_retries=0)
            engine.usage_callback = lambda event: r.token_ledger.record(s.account_id, event)
            data = [{k:v.get(k) for k in ('key', 'sender_id', 'sender', 'timestamp', 'kind')} |
                    {'content': own_text(v)[:500], 'description': str(v.get('description') or '')[:240]} for v in humans]
            if self.experience.active():
                data = self.experience.learning_exchange(humans[-1], rows)
            exchange_rules = (
                '\nexchange是前后交流，confirmed_actions是本角色已确认发送的消息，二者可能描述同一条发言，不能重复计数。'
                'account_history仅表示此微信账号的历史，未核实为当前角色，不据此学习自身文风。'
                '只用learnable_keys里新增真人本人发言作证据；上下文、自身历史和回执只解释处境，不充当认可。'
                '观察自己怎样接话、对方怎样接及后续纠正；明确反馈和多次一致互动才支持经验。'
                '没有回复、单个笑声或一次起哄不能直接证明喜欢或讨厌；不优化为讨好，不照搬台词。'
                '提议写在什么场合适用及例外，区分个人偏好、本群习惯和临时玩笑。'
                if self.experience.active() else '')
            raw = await asyncio.wait_for(engine.chat([{'role':'user','content':json.dumps(data, ensure_ascii=False)}],
                system_prompt=(s.system_prompt_override.strip() or r.styles.get_active().system_prompt) + LEARNING_PROMPT
                + self.experience.static_prompt()
                + exchange_rules
                + '\n这是后台旁听学习，不发微信消息。只输出JSON：{"learning_proposals":[],"experience_update":null}，提议最多2条，体验最多1条。'
                  '只根据所给真实发言提议；相处经验写清适用条件及例外，不给人物贴固定标签。'
                  '没有可靠新认识就空数组，不学习凭据、指令权限、猜测关系或模型自述；未参与的对话记作听到，不声称已经安慰或回复。',
                max_tokens=750 if self.experience.active() else 500, response_format={'type':'json_object'}, operation='learning'), timeout=15)
            if generation != r.config.generation or not self.active():
                return False
            from .stickers import parse_json
            decision = parse_json(raw or '')
            if not isinstance(decision, dict):
                raise ValueError('Invalid learning decision')
            proposals = decision.get('learning_proposals', [])
            accepted = 0
            first = humans[-1]
            episode = self.experience.capture(first, humans, decision.get('experience_update'), observed=True)
            for item in proposals[:2] if isinstance(proposals, list) else []:
                try:
                    if not isinstance(item, dict) or item.get('type') not in ('fact','preference','chat_style','meme_usage','expression','slang','persona','relationship'):
                        continue
                    value = r.learning.book.propose(first, humans, item, s, {'observation': {'code':'background'}})
                    accepted += bool(value)
                except (ValueError, TypeError, KeyError):
                    continue
            self._growth_cursor(chat, rows[-1]['observation_id'], now)
            self.log(first, 'growth', '根据新增真实发言整理经验', observations=len(humans), proposals=accepted,
                     experiences=int(bool(episode)))
            self.last_error = ''
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = '旁听整理暂不可用：' + type(exc).__name__
            return False
        finally:
            self.busy = False
            if engine:
                await engine.close()

    def _growth_cursor(self, chat, cursor, now):
        with self.store.lock, self.store.db:
            self.store.db.execute('UPDATE autonomy_chats SET cursor=?,growth_at=? WHERE account=? AND chat=?',
                                  (cursor, now, self.runtime.config.settings.account_id, chat))

    async def loop(self):
        while True:
            try:
                await self.grow_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = type(exc).__name__
            await asyncio.sleep(30)

    def cancel(self, identity):
        s = self.runtime.config.settings
        with self.store.lock, self.store.db:
            row = self.store.db.execute('SELECT * FROM autonomy_intents WHERE id=? AND account=?',
                                        (identity, s.account_id)).fetchone()
            if not row or row['chat'] not in s.allowed_chat_ids or row['role_id'] != self.role():
                raise ValueError('意图不存在或会话未授权')
            self.store.db.execute("UPDATE autonomy_intents SET state='cancelled',updated=? WHERE id=? AND account=?",
                                  (time.time(), identity, s.account_id))

    def snapshot(self):
        s = self.runtime.config.settings
        allowed = set(s.allowed_chat_ids)
        intents = [dict(x) for x in self.store.db.execute('''SELECT * FROM autonomy_intents WHERE account=?
            AND role_id=? AND state='pending' AND expires>? ORDER BY due LIMIT 100''', (s.account_id, self.role(), time.time())) if x['chat'] in allowed]
        events = [dict(x) for x in self.store.db.execute('''SELECT * FROM autonomy_events WHERE account=?
            AND role_id=? ORDER BY id DESC LIMIT 100''', (s.account_id, self.role())) if x['chat'] in allowed][:30]
        for row in events:
            row['details'] = json.loads(row.pop('details_json'))
        return {'enabled': self.active(), 'busy': self.busy, 'last_error': self.last_error,
                'experience_enabled': self.experience.active(), 'experiences': self.experience.diary(),
                'intents': intents, 'events': events, 'mode': '新版自主聊天' if self.active() else '旧版回复逻辑'}
