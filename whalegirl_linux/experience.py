"""Grounded social context and episodes in the existing, reversible memory book.

No model, timer, outbound action, or second archive lives here. Human observations,
model impressions and delivery receipts keep separate provenance.
"""
from __future__ import annotations

import hashlib
import json
import statistics
import time
from datetime import datetime, timedelta, timezone

from .learning import POISON_PATTERN
from .memory import own_text
from .topic_relevance import current_query, related_topic


STATIC_PROMPT = '''
【体验与相处】真实少女活人感优先：看清语境自然接话或旁观，不背固定台词，不虚构过去经历。
可选JSON扩展字段（无变动留空）：
1. experience_update：记录值得留存的一次真实人际见闻：
   {"source_key":"本轮真人消息key","evidence":"本人原文中的逐字依据","summary":"160字内记录收到的事情及相处感受","feeling":"可选的短感受"}
   尚在发展的事件可带 phase="open|uncertain|resolved"；延续已知事件带 continues_id="该ID"，话题结束或解决用 resolved。不必每轮留档。
2. intent_ops的plan可带 kind="unsaid"（遇到相关话题再接的想法，after_minutes可为0）或 kind="followup"（未来关心）。
3. learning_proposals：相处改进的新认识，写明适用场景与例外。
'''


def active(settings):
    return bool(settings.autonomy_enabled and getattr(settings, 'experience_growth_enabled', True))


def short(value, maximum):
    return value.strip() if isinstance(value, str) and 0 < len(value.strip()) <= maximum else ''


def metadata(row):
    try:
        value = json.loads(row.get('projection_json') or '{}')
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def memory_brief(row):
    """Only these public facts about an episode enter the shared recall budget."""
    data = metadata(row)
    brief = {'feeling': data.get('feeling', ''), 'delivery': data.get('delivery', 'observed'),
             'occurred': data.get('occurred', row.get('created', 0))}
    if data.get('phase'):
        brief.update(phase=data['phase'], participants=data.get('participants', []))
    return brief


class ExperienceService:
    def __init__(self, runtime):
        self.runtime, self.store = runtime, runtime.store
        # Standalone legacy users of AutonomyService may only provide a store.
        # Reuse its canonical book, never create an alternative memory store.
        from .memory import MemoryBook
        self.book = getattr(runtime, 'memory_book', None) or MemoryBook(self.store)
        self.hub = self.book.hub
        if self.hub.config is None:
            self.hub.config = runtime.config
        with self.store.lock, self.store.db:
            self.store.db.execute('''CREATE INDEX IF NOT EXISTS memories_experience_turn
                ON memories(account,role_id,chat,source_class,property_key)''')

    @property
    def settings(self):
        return self.runtime.config.settings

    def active(self):
        return active(self.settings)

    def remembering(self):
        s = self.settings
        return self.active() and s.memory_enabled and s.memory_consolidation_enabled and s.memory_auto_capture

    def permitted(self, first):
        return (first.get('account') == self.settings.account_id
                and first.get('chat') in self.settings.allowed_chat_ids)

    def static_prompt(self):
        return STATIC_PROMPT if self.active() else ''

    def social_prompt(self, first, batch, *, now=None):
        """Describe observations, never infer intent, emotion or a response by keywords."""
        if not self.active() or not self.permitted(first):
            return ''
        now = time.time() if now is None else now
        rows = self.store.conversation(first['account'], first['chat'], 80)
        rows = [r for r in rows if r['account'] == first['account'] and r['chat'] == first['chat']
                and 0 <= now - r['timestamp'] <= 86400]
        # Deduplicate gateway/UI projections by the durable message identity.
        unique = {(r.get('sender_id'), r.get('local_id') or r['key']): r for r in rows}
        rows = sorted(unique.values(), key=lambda r:(r['timestamp'], r.get('local_id') or 0))
        human = [r for r in rows if not r['is_self'] and r.get('sender_id')]
        if not human:
            return ''
        minute = [r for r in rows if now - r['timestamp'] <= 90]
        voices = {r['sender_id'] for r in minute if not r['is_self']}
        bot_count = sum(bool(r['is_self']) for r in minute)
        lengths = [len(own_text(r)) for r in human if r['kind'] == 1 and own_text(r)]
        sticker_count = sum(r['kind'] == 47 for r in human)
        style = ''
        if len(human) >= 8:
            median = round(statistics.median(lengths)) if lengths else 0
            style = f'；近期真人文字中位{median}字，表情{round(100*sticker_count/len(human))}%'
        venue = '群聊' if first['chat'].endswith('@chatroom') else '会话'
        text = f'\n【{venue}实况】近90秒{len(voices)}人参与，你已发{bot_count}条{style}。'
        # No random echo gate: offer a real formation to the same model once.
        from .conversation import repeat_candidate
        repeat = repeat_candidate(rows, now)
        if repeat and repeat['kind'] == 47:
            formation = [r for r in rows if now-r['timestamp'] < 45 and r['kind'] in (1,47)][-3:]
            identities = []
            for item in formation:
                try:
                    payload = json.loads(item.get('payload_json') or '{}')
                    identities.append((payload.get('sticker') or {}).get('md5', ''))
                except (ValueError, TypeError, AttributeError):
                    identities.append('')
            if not identities or any(not identity for identity in identities) or len(set(identities)) != 1:
                repeat = None
        if repeat:
            value = own_text(repeat) if repeat['kind'] == 1 else '原生表情队形（只选对应原生表情，不发含义标签）'
            text += '不同群友正在同一队形：' + json.dumps(value[:100], ensure_ascii=False) + '，可以顺势跟一次，也可以不跟。'
        return text

    def _turn_property(self, first):
        raw = json.dumps([self.hub.role(), first['account'], first['chat'], first['key']], ensure_ascii=False)
        return 'experience.' + hashlib.sha256(raw.encode()).hexdigest()[:40]

    def current_events(self, first, *, now=None):
        """A small live view of canonical episodes, not a parallel event memory."""
        if not self.remembering() or not self.permitted(first):
            return []
        now = time.time() if now is None else now
        rows = self.store.db.execute('''SELECT * FROM memories WHERE account=? AND chat=? AND role_id=?
            AND source_class='experience' AND archived=0 AND status='active' AND tier!='cold'
            AND (expires_at=0 OR expires_at>?) AND updated>=? ORDER BY updated DESC,id DESC LIMIT 24''',
            (first['account'], first['chat'], self.hub.role(), now, now-1800)).fetchall()
        result = []
        for raw in rows:
            row = dict(raw)
            info = metadata(row)
            if info.get('phase') not in ('open', 'uncertain'):
                continue
            if not 0 <= now-info.get('last_event_at', row['created']) <= 1800:
                continue
            source = self.hub.source(first['account'], row['source_key'])
            if (not source or source['chat'] != first['chat'] or source['is_self']
                    or not source['sender_id'] or self.hub.blocked(first['account'], first['chat'],
                        row['subject'], row['content'], row['source_key'], row['kind'])):
                continue
            result.append(row)
            if len(result) == 2:
                break
        return result

    def interaction_prompt(self, first):
        rows = self.current_events(first)
        first['_interaction_versions'] = {r['id']:r['version'] for r in rows if not r['locked']}
        if not rows:
            return ''
        brief = [{'id':r['id'], '目前理解':r['content'][:160],
                  'phase':metadata(r)['phase'], '参与者':metadata(r).get('participants', []),
                  '来源':r['source_key']} for r in rows]
        return ('\n【当前尚在发展的事情｜暂时理解，可被新消息修正，不是待办】'
                + json.dumps(brief, ensure_ascii=False))

    def learning_exchange(self, first, rows):
        """Bounded observed dialogue plus confirmed receipts for this role.

        Only new, actual human sources can support a learning proposal. Prior
        turns and our own messages explain context; they are never new feedback.
        """
        if not self.remembering() or not self.permitted(first):
            return {'exchange':[], 'learnable_keys':[], 'confirmed_actions':[]}
        now = time.time()
        suppressed = set(self.book.suppressed_sources(first['account'], [first['chat']]))
        rows = [r for r in rows if r['account'] == first['account'] and r['chat'] == first['chat']
                and r['key'] not in suppressed and 0 <= now-r['timestamp'] <= 86400]
        humans = [r for r in rows if not r['is_self'] and r.get('sender_id')]
        if not humans:
            return {'exchange':[], 'learnable_keys':[], 'confirmed_actions':[]}
        start = min((r['timestamp'], r.get('local_id') or 0) for r in rows)
        end = max(r['timestamp'] for r in humans)
        history = [r for r in self.store.conversation(first['account'], first['chat'], 80)
                   if (r['timestamp'], r.get('local_id') or 0) < start
                   and r['key'] not in suppressed and r['timestamp'] >= start[0]-600][-8:]
        own = [dict(r) for r in self.store.db.execute('''SELECT * FROM message_log WHERE account=? AND chat=?
            AND is_self=1 AND timestamp>=? AND timestamp<=? ORDER BY timestamp DESC,local_id DESC LIMIT 20''',
            (first['account'],first['chat'],start[0],end))]
        ordered = sorted((r for r in history+rows+own if r['key'] not in suppressed),
                         key=lambda r:(r['timestamp'],r.get('local_id') or 0))
        unique = {(r.get('local_id') or r['key'],r['is_self']):r for r in ordered}
        eligible = {r['key'] for r in humans}
        exchange = []
        for row in unique.values():
            exchange.append({'key':row['key'], 'sender_id':row['sender_id'], 'sender':row['sender'],
                'timestamp':row['timestamp'], 'kind':row['kind'],
                'speaker_role':'account_history' if row['is_self'] else 'human',
                'learnable_source':row['key'] in eligible,
                'content':own_text(row)[:500], 'image_note':str(row.get('description') or '')[:160]})
        actions = self.store.db.execute('''SELECT a.inbox_key,a.kind,a.reply,a.sent_at FROM outbound_actions a
            WHERE a.account=? AND a.chat=? AND a.state='sent' AND a.sent_at>=? AND a.sent_at<=?
            AND EXISTS(SELECT 1 FROM autonomy_events e WHERE e.account=a.account AND e.chat=a.chat
                AND e.source_key=a.inbox_key AND e.role_id=? AND e.kind='speak')
            ORDER BY a.sent_at DESC,a.id DESC LIMIT 8''',
            (first['account'],first['chat'],start[0]-120,end,self.hub.role())).fetchall()
        confirmed = [{'reply_to':a['inbox_key'],'type':a['kind'],'content':a['reply'][:240],
                      'sent_at':a['sent_at']} for a in reversed(actions)]
        return {'exchange':exchange,'learnable_keys':list(dict.fromkeys(r['key'] for r in humans)),
                'confirmed_actions':confirmed}

    def emotional_context(self, first, *, now=None):
        """Keep a fading mood's actual cause, without assigning it to a new speaker.

        Use the existing mood clock and event ledger. Observing more messages must
        not renew an emotion; a reset or later neutral update must not revive it.
        """
        if not self.active() or not self.permitted(first) or not self.settings.mood_engine_enabled:
            return ''
        from .conversation import current_mood_state
        now = time.time() if now is None else now
        mood = current_mood_state(self.store, self.settings, first['account'], first['chat'], now=now)
        horizon = max(5, int(self.settings.mood_half_life_minutes or 25)) * 60 * 1.35
        if mood['mood'] == 'normal' or not 0 <= now-mood['updated'] <= horizon:
            return ''
        row = self.store.db.execute('''SELECT * FROM autonomy_events WHERE account=? AND chat=?
            AND kind='mood_changed' ORDER BY created DESC,id DESC LIMIT 1''',
            (first['account'], first['chat'])).fetchone()
        if not row or row['role_id'] != self.hub.role():
            return ''
        try:
            detail = json.loads(row['details_json'])
        except (ValueError, TypeError):
            return ''
        if (not isinstance(detail, dict) or detail.get('mood') != mood['mood']
                or detail.get('state_updated') != mood['updated']):
            return ''
        source = self.hub.source(first['account'], row['source_key'])
        if (not source or source['chat'] != first['chat'] or source['is_self']
                or not source['sender_id'] or source['timestamp'] > now
                or source['timestamp'] < mood['updated'] - 86400):
            return ''
        cause = {'来源发言者':source['sender_id'], '原话':own_text(source)[:100],
                 '当时感受':mood['reason'][:80], '距今秒':round(max(0, now-mood['updated']))}
        return '\n【互动余韵｜已发生的来由，不是当前新消息】' + json.dumps(cause, ensure_ascii=False)

    def capture(self, first, batch, value, *, observed=False):
        if not self.remembering() or not self.permitted(first) or not isinstance(value, dict):
            return None
        key = value.get('source_key')
        if not isinstance(key, str):
            return None
        offered = {r['key'] for r in batch if not r.get('is_self') and r.get('sender_id')
                   and r.get('account') == first['account'] and r.get('chat') == first['chat']}
        if key not in offered:
            return None
        source = self.hub.source(first['account'], key)
        if not source or source['chat'] != first['chat'] or source['is_self'] or not source['sender_id']:
            return None
        if self.hub.forgotten_source(first['account'], key):
            return None
        evidence, summary = short(value.get('evidence'), 240), short(value.get('summary'), 160)
        feeling = short(value.get('feeling'), 40)
        if (len(evidence) < 4 or evidence not in own_text(source) or len(summary) < 6
                or POISON_PATTERN.search(evidence + summary + feeling)):
            return None
        phase = value.get('phase')
        if phase is not None and phase not in ('open', 'uncertain', 'resolved'):
            return None
        old = None
        continuation = value.get('continues_id')
        if continuation is not None:
            versions = first.get('_interaction_versions') or {}
            if isinstance(continuation, bool) or not isinstance(continuation, int) or continuation not in versions:
                return None
            old = self.book.get(first['account'], continuation, [first['chat']])
            if (not old or old['version'] != versions[continuation] or old['locked'] or old['archived']
                    or old['role_id'] != self.hub.role() or old['source_class'] != 'experience'
                    or old['status'] != 'active' or phase is None
                    or metadata(old).get('phase') not in ('open','uncertain')):
                return None
            previous_source = self.hub.source(first['account'], old['source_key'])
            if previous_source and (source['timestamp'],source.get('local_id') or 0) <= (
                    previous_source['timestamp'],previous_source.get('local_id') or 0):
                return None
        origin = hashlib.sha256(json.dumps([self.hub.role(), first['chat'], source.get('local_id') or key]).encode()).hexdigest()
        with self.store.lock, self.store.db:
            if self.store.db.execute("SELECT 1 FROM memory_origins WHERE account=? AND source='experience' AND origin_id=?",
                                     (first['account'], origin)).fetchone():
                return None
            existing = self.store.db.execute('''SELECT id FROM memories WHERE account=? AND chat=? AND role_id=?
                AND source_class='experience' AND (source_key=? OR property_key=?) LIMIT 1''',
                (first['account'], first['chat'], self.hub.role(), key, self._turn_property(first))).fetchone()
            if existing:
                # MemoryBook commits its revision before the compatibility origin.
                # Recover that gap without recreating or reactivating the memory.
                self.store.db.execute("INSERT OR IGNORE INTO memory_origins VALUES(?,'experience',?,?,1)",
                                      (first['account'], origin, existing['id']))
                return None
            if self.hub.blocked(first['account'], first['chat'], source['sender_id'], summary, key, 'shared_moment'):
                return None
            info = {'feeling': feeling, 'delivery': 'observed' if observed else 'planning',
                    'occurred':metadata(old).get('occurred', source['timestamp']) if old else source['timestamp'],
                    'evidence':evidence}
            if phase:
                people = (metadata(old).get('participants', []) if old else []) + [source['sender_id']]
                info.update(phase=phase, participants=list(dict.fromkeys(people))[-5:],
                            last_event_at=source['timestamp'])
            change = {
                'chat': first['chat'], 'subject': old['subject'] if old else source['sender_id'], 'kind': 'shared_moment',
                'scope': 'chat', 'content': summary, 'source_key': key,
                'property_key': old['property_key'] if old else self._turn_property(first),
                'tier':old['tier'] if old else 'ordinary',
                'status': 'active' if observed else 'pending',
                '_experience':info}
            if old:
                change.update(op='update', id=old['id'], version=old['version'])
            row = self.book.change(first['account'], [first['chat']], change, actor='experience')
            self.store.db.execute("INSERT INTO memory_origins VALUES(?,'experience',?,?,1)",
                                  (first['account'], origin, row['id']))
            first['_experience_pending'] = [row['id']]
            return row

    def settle(self, first, outcome):
        """Activate received experiences with the actual, separate outbound status."""
        if not self.active() or not self.permitted(first):
            return
        pending = [v for v in first.get('_experience_pending', []) if isinstance(v, int) and not isinstance(v, bool)][:2]
        row = self.store.db.execute('''SELECT * FROM memories WHERE account=? AND chat=? AND role_id=?
            AND source_class='experience' AND property_key=? AND archived=0
            AND status IN ('active','pending') ORDER BY id DESC LIMIT 1''',
            (first['account'], first['chat'], self.hub.role(), self._turn_property(first))).fetchone()
        if pending:
            row = self.store.db.execute('''SELECT * FROM memories WHERE id=? AND account=? AND chat=? AND role_id=?
                AND source_class='experience' AND archived=0 AND status IN ('active','pending')''',
                (pending[0], first['account'], first['chat'], self.hub.role())).fetchone()
        if not row:
            return
        old = dict(row)
        record = self.store.get(first['key'])
        state = (record or {}).get('state', '')
        delivery = {'silent': 'observed', 'image_queued': 'queued'}.get(outcome)
        if delivery is None:
            delivery = state if state in ('sent','partial','uncertain','failed','skipped') else 'unconfirmed'
        data = metadata(old)
        if data.get('delivery') == delivery and old['status'] == 'active':
            return
        data['delivery'] = delivery
        with self.store.lock, self.store.db:
            changed = self.store.db.execute('''UPDATE memories SET projection_json=?,status='active',version=version+1
                WHERE id=? AND version=?''', (json.dumps(data, ensure_ascii=False), old['id'], old['version']))
            if not changed.rowcount:
                return
            after = dict(self.store.db.execute('SELECT * FROM memories WHERE id=?', (old['id'],)).fetchone())
            self.book._revision(first['account'], after, old, 'experience', 'delivery_recorded')

    def diary(self, limit=40):
        """An admin view of canonical memories, not a second copy of private chats."""
        s = self.settings
        if not s.allowed_chat_ids:
            return []
        marks = ','.join('?' for _ in s.allowed_chat_ids)
        rows = self.store.db.execute(f'''SELECT * FROM memories WHERE account=? AND role_id=?
            AND chat IN ({marks}) AND source_class='experience' AND archived=0 AND status='active'
            AND (expires_at=0 OR expires_at>?) ORDER BY created DESC,id DESC LIMIT ?''',
            (s.account_id, self.hub.role(), *s.allowed_chat_ids, time.time(), min(60, max(1, limit))))
        result = []
        for raw in rows:
            row = dict(raw)
            result.append({**{k:row[k] for k in ('id','version','chat','subject','content','created','source_key')},
                           **memory_brief(row), 'day': datetime.fromtimestamp(
                               row['created'], timezone(timedelta(hours=8))).strftime('%Y-%m-%d')})
        return result

    def fresh_reading(self, first):
        """One genuine public observation can provide a new, optional social opening."""
        s = self.settings
        if not self.active() or not self.permitted(first) or not s.memory_enabled or not s.growth_enabled:
            return None
        rows = self.store.db.execute('''SELECT m.*,i.topic,i.journal_id,s.kind AS reading_kind,s.url,s.title
            FROM growth_insights i JOIN memories m ON m.id=i.memory_id
            JOIN growth_sources s ON s.id=substr(m.source_key,7) AND s.account=m.account AND s.role_id=m.role_id
            WHERE m.account=? AND m.role_id=?
            AND m.source_class='world_learning' AND m.status='active' AND m.archived=0 AND m.tier!='cold'
            AND m.created>? AND NOT EXISTS(SELECT 1 FROM autonomy_events e WHERE e.account=m.account
                AND e.role_id=m.role_id AND e.chat=? AND e.kind='reading_considered' AND e.source_key='reading:'||m.id)
            ORDER BY m.created DESC,m.id DESC LIMIT 20''',
            (s.account_id, self.hub.role(), time.time()-86400, first['chat'])).fetchall()
        for raw in rows:
            row = dict(raw)
            from .growth import tieba_url
            if row['reading_kind'] == 'tieba' or tieba_url(row['url']):
                if not (s.growth_tieba_enabled and s.tieba_enabled):
                    continue
            elif not s.growth_web_enabled:
                continue
            return row
        return None

    def reading_prompt(self, first):
        row = self.fresh_reading(first)
        if not row:
            return ''
        # Bind consumption to the exact item offered, not a later completed read.
        first['_reading_offered'] = row['id']
        return '\n【实际读过的新见闻】' + json.dumps({'id':'memory:'+str(row['id']),
            'topic':row['topic'], 'understanding':row['content'][:160], 'read_at':row['created'],
            'title':row['title'], 'url':row['url']},
            ensure_ascii=False) + '。这是公开阅读所得，可以自然聊到或沉默；需要原文与链接时调用记忆get工具，不当作亲身经历。'

    def consume_reading(self, first):
        identity = first.get('_reading_offered')
        if not self.active() or not self.permitted(first) or not isinstance(identity, int):
            return
        row = self.store.db.execute('''SELECT 1 FROM memories WHERE id=? AND account=? AND role_id=?
            AND source_class='world_learning' AND status='active' AND archived=0''',
            (identity, first['account'], self.hub.role())).fetchone()
        if row:
            with self.store.lock, self.store.db:
                self.store.db.execute('''INSERT INTO autonomy_events
                    (account,chat,source_key,kind,reason,details_json,created,role_id)
                    SELECT ?,?,?,'reading_considered','本轮已考虑公开见闻，不重复催促分享','{}',?,?
                    WHERE NOT EXISTS(SELECT 1 FROM autonomy_events WHERE account=? AND chat=?
                    AND role_id=? AND kind='reading_considered' AND source_key=?)''',
                    (first['account'], first['chat'], 'reading:'+str(identity), time.time(), self.hub.role(),
                     first['account'], first['chat'], self.hub.role(), 'reading:'+str(identity)))
