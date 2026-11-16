"""Opt-in, low-priority conversation starters using the existing send ledger.

Scheduler events are never archived as incoming WeChat messages or memory
evidence. A restart never replays a planned opener or an uncertain send.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from .conversation import anti_formula_check, habit_static_prompt, pair_sticker, pat_target_options, plan_actions, plan_pat_targets, sticker_dynamic_prompt, sticker_static_prompt, sticker_usage_prompt, warmth_prompt
from .memory import memory_parts
from .mentions import apply_mentions, mention_dynamic_prompt, mention_options, mention_static_prompt
from .stickers import parse_json
from .resources import native_pressure
from .context import build_context, vision_reply
from .search import DeepSeekSearch, search_static_prompt
from .proactive_tools import resolve_tools
from .usage_context import scoped_usage, set_usage_context
from .settings import reply_temperature
from .model_reply import guarded_chat, ToolReplyFormatError

BEIJING = timezone(timedelta(hours=8))


def active_hour(settings, now):
    hour = datetime.fromtimestamp(now, BEIJING).hour
    start, end = settings.proactive_start_hour, settings.proactive_end_hour
    return start == end or (start <= hour < end if start < end else hour >= start or hour < end)


def day_start(now):
    return datetime.fromtimestamp(now, BEIJING).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


class ProactiveService:
    def __init__(self, runtime):
        self.runtime = runtime
        self.store, self.config = runtime.store, runtime.config
        self.busy = False
        self.last_error = ''
        self.native_tools_disabled_generation = None
        self.started_at = time.time()
        with self.store.lock:
            self.store.db.executescript("""
                CREATE TABLE IF NOT EXISTS proactive_chats(
                    account TEXT,chat TEXT,last_decision REAL NOT NULL DEFAULT 0,
                    last_start REAL NOT NULL DEFAULT 0,waiting_after REAL NOT NULL DEFAULT 0,
                    session_until REAL NOT NULL DEFAULT 0,partners TEXT NOT NULL DEFAULT '[]',
                    paused_until REAL NOT NULL DEFAULT 0,pause_reason TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(account,chat));
                CREATE TABLE IF NOT EXISTS proactive_runs(
                    id TEXT PRIMARY KEY,account TEXT NOT NULL,chat TEXT NOT NULL,
                    state TEXT NOT NULL,reason TEXT NOT NULL DEFAULT '',
                    sources TEXT NOT NULL DEFAULT '[]',created REAL NOT NULL,updated REAL NOT NULL,
                    image_job TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS proactive_runs_scope ON proactive_runs(account,chat,created);
                CREATE TABLE IF NOT EXISTS proactive_people(
                    account TEXT,chat TEXT,subject TEXT,paused_until REAL NOT NULL,reason TEXT NOT NULL,
                    PRIMARY KEY(account,chat,subject));
            """)
            # A model decision is expendable. Never replay one after a restart.
            self.store.db.execute("""UPDATE proactive_runs SET state='skipped',error='核心重启，未补发旧主动任务',
                updated=? WHERE state='planning'""", (time.time(),))
            self.store.db.commit()

    def execute(self, sql, args=()):
        with self.store.lock:
            result = self.store.db.execute(sql, args)
            self.store.db.commit()
            return result

    def chat_state(self, chat):
        row = self.store.db.execute('SELECT * FROM proactive_chats WHERE account=? AND chat=?',
                                   (self.config.settings.account_id, chat)).fetchone()
        return dict(row) if row else dict(last_decision=0, last_start=0, waiting_after=0,
            session_until=0, partners='[]', paused_until=0, pause_reason='')

    def touch(self, chat, **fields):
        if set(fields) - {'last_decision', 'last_start', 'waiting_after', 'session_until',
                          'partners', 'paused_until', 'pause_reason'}:
            raise ValueError('Unknown proactive state field')
        account = self.config.settings.account_id
        self.execute('INSERT OR IGNORE INTO proactive_chats(account,chat) VALUES(?,?)', (account, chat))
        self.execute('UPDATE proactive_chats SET '+','.join(k+'=?' for k in fields)+' WHERE account=? AND chat=?',
                     (*fields.values(), account, chat))

    def permitted(self, chat, now=None):
        s = self.config.settings
        now = time.time() if now is None else now
        return bool(s.enabled and s.proactive_enabled and s.account_id and chat in s.allowed_chat_ids
            and chat in s.proactive_chat_ids
            and (chat.endswith('@chatroom') or s.proactive_private_enabled)
            and (chat not in s.trigger_only_chat_ids or chat in s.proactive_strict_chat_ids)
            and self.chat_state(chat)['paused_until'] <= now
            and not self.runtime.chat_control.active(s.account_id,chat))

    def muted_people(self, chat, now=None):
        return {r[0] for r in self.store.db.execute("""SELECT subject FROM proactive_people
            WHERE account=? AND chat=? AND paused_until>?""",
            (self.config.settings.account_id, chat, time.time() if now is None else now))}

    def continuation(self, event, now=None):
        now = time.time() if now is None else now
        if not self.permitted(event['chat'], now) or not event.get('sender_id'):
            return False
        state = self.chat_state(event['chat'])
        return (state['session_until'] > now and state['waiting_after'] <= event['timestamp']
            and event['sender_id'] in json.loads(state['partners'])
            and event['sender_id'] not in self.muted_people(event['chat'], now))

    def observe(self, event, raw):
        """Honor direct requests to pause without inventing a memory fact."""
        if raw.get('isSelf') is not False or event['chat'] not in self.config.settings.proactive_chat_ids:
            return
        # Only the actual sender's words count, not a quote/forward payload.
        if raw.get('forwarded') or raw.get('type') != 1:
            return
        text = str(raw.get('content') or '').strip()
        import re
        direct = not event['chat'].endswith('@chatroom') or raw.get('isMentioned') or self.continuation(event)
        if not direct:
            return
        stop = bool(re.search(r'别(?:再)?(?:找我|打扰|主动|发了|说了|聊了|烦我)|不要(?:再)?(?:打扰|主动|找我)|不想聊|先别聊|安静点|暂停聊天', text))
        busy = bool(re.search(r'(?:我|现在|今天|这会儿).{0,6}(?:在忙|很忙|没空|要睡|睡觉)|晚点(?:再)?聊|改天聊|先忙', text))
        if not (stop or busy):
            return
        until = time.time() + (self.config.settings.proactive_pause_hours * 3600 if stop else 2 * 3600)
        reason = '对方明确要求暂停主动联系' if stop else '对方表示忙碌或稍后再聊'
        subject = event.get('sender_id')
        if subject:
            self.execute('INSERT OR REPLACE INTO proactive_people VALUES(?,?,?,?,?)',
                         (event['account'], event['chat'], subject, until, reason))
        if not event['chat'].endswith('@chatroom') or stop:
            self.touch(event['chat'], paused_until=until, pause_reason=reason, session_until=0)

    def pause(self, chat, resume=False):
        if chat not in self.config.settings.allowed_chat_ids:
            raise ValueError('会话未授权')
        self.touch(chat, paused_until=0 if resume else time.time()+self.config.settings.proactive_pause_hours*3600,
                   pause_reason='' if resume else '管理员暂停主动聊天', session_until=0)
        if resume:
            self.execute('UPDATE proactive_people SET paused_until=0 WHERE account=? AND chat=?',
                         (self.config.settings.account_id, chat))

    def quota(self, chat, now):
        s = self.config.settings
        rows = self.store.db.execute("""SELECT chat,count(*) AS n FROM proactive_runs
            WHERE account=? AND created>=? AND state IN ('sending','sent','partial','uncertain','handled')
            GROUP BY chat""", (s.account_id, day_start(now))).fetchall()
        counts = {r['chat']: r['n'] for r in rows}
        return (sum(counts.values()) < s.proactive_per_day
                and (not s.proactive_chat_per_day or counts.get(chat, 0) < s.proactive_chat_per_day))

    def latest_human(self, chat):
        row = self.store.db.execute("""SELECT * FROM message_log WHERE account=? AND chat=? AND is_self=0
            AND sender_id!='' ORDER BY timestamp DESC,local_id DESC LIMIT 1""",
            (self.config.settings.account_id, chat)).fetchone()
        return dict(row) if row else None

    def blocked(self):
        r = self.runtime
        # Slow image API work is independent; ui_busy serializes native sends.
        return (r.processing or r.chat_turn_lock.locked() or r.store.queue() or r.paused_reason
            or r.safety.held(self.config.settings.account_id)
            or r.moments.lock.locked() or self.ui_busy() or native_pressure(self.config))

    def ui_busy(self):
        gateway = self.runtime.gateway
        lock = getattr(gateway, 'ui_lock', None)
        return (lock is not None and lock.locked()) or bool(getattr(gateway, 'inflight_sends', 0))

    def decision_gap(self):
        s = self.config.settings
        return s.proactive_decision_minutes * 60 * {'quiet': 2, 'balanced': 1, 'active': .5}[s.proactive_level]

    def private_idle_at(self, chat, state, last):
        """An opted-in private chat can become an idle opportunity without a new anchor.

        Count from both real conversation activity and the last decision, so a
        silent model decision cannot turn into repeated timer calls. This does
        not grant contact permission or bypass the unanswered-opener check.
        """
        if chat.endswith('@chatroom') or not last:
            return None
        s = self.config.settings
        gap = max(self.decision_gap(), s.proactive_cooldown_minutes * 60,
                  s.proactive_idle_minutes * 60)
        previous = self.store.db.execute('SELECT state,created FROM proactive_runs WHERE account=? AND chat=? '
            'ORDER BY created DESC LIMIT 1', (s.account_id, chat)).fetchone()
        # An opener cancelled for foreground work is not an AI choice to stay
        # silent. It may be reconsidered at the normal decision interval.
        decision_gap = self.decision_gap() if previous and previous['state'] == 'skipped' and (
            previous['created'] >= state['last_decision'] - .001) else gap
        return max(state['last_decision'] + decision_gap,
                   max(state['last_start'], last['timestamp'], self.store.latest_sent(s.account_id, chat)) + gap)

    async def private_send_slot(self, first, generation, diagnostics, *, timeout=30):
        """Wait without a UI/chat lock; discard immediately if this chat changes."""
        started = time.monotonic()
        outcome = 'cancelled'
        try:
            anchor = json.loads(first.get('payload_json') or '{}').get('anchor')
            while True:
                if generation != self.config.generation or not self.send_allowed(first, started=True):
                    outcome = 'permission_changed';return False
                if anchor != self.source_anchor(first['chat']):
                    outcome = 'context_changed';return False
                if not self.quota(first['chat'], time.time()):
                    outcome = 'quota';return False
                if self.send_allowed(first) and not self.blocked():
                    outcome = 'ready';return True
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    outcome = 'timeout';return False
                await asyncio.sleep(min(1, remaining))
        finally:
            diagnostics['proactive_send_wait'] = {'outcome':outcome,
                'elapsed_ms':round((time.monotonic()-started)*1000)}

    def eligible(self, chat, now):
        s = self.config.settings
        if not self.permitted(chat, now) or not active_hour(s, now) or not self.quota(chat, now):
            return False
        if not self.store.baseline_exists(s.account_id, chat):
            return False
        state = self.chat_state(chat)
        last = self.latest_human(chat)
        if not last or (not chat.endswith('@chatroom') and last['sender_id'] in self.muted_people(chat, now)):
            return False
        if chat.endswith('@chatroom'):
            import re
            last_txt = str(last.get('content') or '').strip()
            self_names = {s.account_id, '沃不明白', '小鲸鱼', '大肥鱼', *getattr(s, 'keywords', ())}
            if last.get('sender_id') in getattr(s, 'fishtalk_excluded_sender_ids', ()):
                return False
            if re.search(r'(?<![A-Za-z0-9._%+-])@\S+', last_txt) and not any(k and k in last_txt for k in self_names):
                return False
        if self.runtime.autonomy.active() and not self.runtime.autonomy.opportunity(chat, anchor=last['key'], now=now):
            idle_at = self.private_idle_at(chat, state, last)
            if idle_at is None or now < idle_at:
                return False
        due = self.runtime.autonomy.pending(s.account_id, chat, due=True, now=now)
        # Do not message unfamiliar contacts or repeatedly ping an unanswered opener.
        if (not due and state['waiting_after'] and last['timestamp'] <= state['waiting_after']):
            return False
        return (now >= max(state['last_decision'] + self.decision_gap(),
                           state['last_start'] + s.proactive_cooldown_minutes * 60,
                           last['timestamp'] + s.proactive_idle_minutes * 60,
                           self.store.latest_sent(s.account_id, chat) + s.proactive_idle_minutes * 60))

    def source_anchor(self, chat):
        last = self.latest_human(chat)
        return last['key'] if last else ''

    def send_allowed(self, first, *, started=False):
        now = time.time()
        payload = json.loads(first.get('payload_json') or '{}')
        return (first['account'] == self.config.settings.account_id and self.permitted(first['chat'], now)
            and active_hour(self.config.settings, now)
            and (started or not self.store.queue() and payload.get('anchor') == self.source_anchor(first['chat']))
            and not self.runtime.paused_reason)

    def accepted(self, first, actions):
        now = time.time()
        receipts = [a['sent_at'] for a in self.store.actions(first['key']) if a['state'] == 'sent']
        waiting_after = min(receipts) if receipts else now
        partners = {m['id'] for a in actions for m in a.get('mentions', [])}
        if not first['chat'].endswith('@chatroom'):
            last = self.latest_human(first['chat'])
            if last:
                partners.add(last['sender_id'])
        # An untargeted group opener can be answered by recent participants;
        # relevance still goes through the normal model decision.
        if not partners:
            partners.update(r['sender_id'] for r in self.store.conversation(first['account'], first['chat'], 20)
                            if r['is_self'] == 0 and r['sender_id'])
        partners -= self.muted_people(first['chat'], now)
        self.touch(first['chat'], last_start=now, waiting_after=waiting_after,
                   session_until=now+self.config.settings.proactive_continuation_minutes*60,
                   partners=json.dumps(sorted(partners)))

    def finish(self, identity, state, *, reason=None, sources=None, image_job='', error=''):
        fields = dict(state=state, updated=time.time(), error=error[:250])
        if reason is not None:
            fields['reason'] = str(reason)[:300]
        if sources is not None:
            fields['sources'] = json.dumps(sources, ensure_ascii=False)
        if image_job:
            fields['image_job'] = image_job
        self.execute('UPDATE proactive_runs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',
                     (*fields.values(), identity))

    @scoped_usage
    async def run_once(self):
        r, s = self.runtime, self.config.settings
        now = time.time()
        if (not s.proactive_enabled or not s.enabled or not active_hour(s, now) or self.blocked()
                or now-self.started_at < 60 or r.account_id != s.account_id
                or r.gateway_state.get('status') != 'logged_in'):
            return False
        # Persisted round-robin ordering prevents a favorite chat starving others.
        targets = sorted((c for c in s.proactive_chat_ids if self.eligible(c, now)),
                         key=lambda c: (self.chat_state(c)['last_decision'], c))
        last_probe = self.store.db.execute('SELECT max(last_decision) FROM proactive_chats WHERE account=?',
                                          (s.account_id,)).fetchone()[0] or 0
        if not targets or now-last_probe < self.decision_gap():
            return False
        chat = targets[0]
        generation = self.config.generation
        self.touch(chat, last_decision=now)
        identity = uuid.uuid4().hex
        key = 'proactive:'+identity
        self.execute('INSERT INTO proactive_runs(id,account,chat,state,created,updated) VALUES(?,?,?,?,?,?)',
                     (identity, s.account_id, chat, 'planning', now, now))
        recent = self.store.conversation(s.account_id, chat, s.context_messages)
        muted = self.muted_people(chat, now)
        humans = [row for row in recent if row['is_self'] == 0 and row['sender_id'] not in muted]
        if not humans:
            self.finish(identity, 'ignored', reason='当前没有适合主动联系的聊天对象')
            return False
        first = dict(key=key, account=s.account_id, chat=chat, sender='自主聊天调度',
                     sender_id=humans[-1]['sender_id'] if not chat.endswith('@chatroom') else '',
                     kind=1, local_id=0, timestamp=now, trigger='proactive',
                     content='[系统自主开聊候选，不是真实收到的微信消息]',
                     payload_json=json.dumps({'anchor': self.source_anchor(chat)}))
        due_intents = r.autonomy.pending(s.account_id, chat, due=True, now=now)
        set_usage_context(s.account_id, chat, key)
        r.autonomy.considered(first, self.source_anchor(chat))
        self.busy = True
        engine = None
        try:
            query = '\n'.join(row['content'][:350] for row in humans[-6:])
            recall = {}
            memory_static, memory_dynamic, memory_scope = memory_parts(r.memory_book, s, first, humans[-6:], r.affection, recall)
            loaded_memory_ids = set(recall.get('ids', []))
            base_image_catalog = r.images.tools.base_candidates(first)
            unused, image_context = r.images.tools.context(
                first, [first], query=query, base_candidates=base_image_catalog)
            base_image_ids = {item['id'] for item in base_image_catalog}
            used = r.sticker_library.excluded(s.account_id, chat)
            history = self.store.sticker_selection_history(s.account_id, chat, s.sticker_cooldown_seconds) if s.stickers_enabled else {}
            if s.stickers_enabled:
                r.sticker_library.apply_inflight_cooldown(s.account_id, chat, s.sticker_cooldown_seconds, history)
            fresh_bonus = r.sticker_library.fresh_collected_bonus(s.account_id, chat) if s.stickers_enabled else {}
            choices = r.stickers.candidates(query, s.sticker_candidate_limit, used, history, fresh_bonus=fresh_bonus) if s.stickers_enabled else {}
            mentions_enabled = s.proactive_mentions_enabled and s.group_mentions_enabled and chat.endswith('@chatroom')
            mentions = {}
            if mentions_enabled:
                try:
                    roster = await asyncio.wait_for(r.gateway.group_members(chat), timeout=3)
                    mentions = mention_options([m for m in roster if m['id'] not in muted and m['id'] != s.account_id],
                                               [m['sender_id'] for m in humans])
                except Exception:
                    pass
            pat_cooldown_ids = {
                *(getattr(self.store, 'recent_pat_targets', lambda *a, **k: ())(s.account_id, chat, getattr(s, 'pat_cooldown_seconds', 45))),
                *(r.recent_pat_targets(s.account_id, chat) if hasattr(r, 'recent_pat_targets') else ()),
            }
            pat_options = pat_target_options(humans[-12:], account_id=s.account_id, excluded={*muted, *pat_cooldown_ids})
            source_ids = {m['key'] for m in recent} | {'memory:'+str(i) for i in recall.get('ids', [])+recall.get('joke_ids', [])}
            tieba_evidence = {}
            system = ((s.system_prompt_override.strip() or r.styles.get_active().system_prompt)
                + habit_static_prompt(s) + warmth_prompt(s) + memory_static + search_static_prompt(s)
                + r.fishtalk.static_prompt()
                + r.affection.static_prompt(first, s, include_venue=False) + r.sticker_library.static_prompt(s)
                + r.images.tools.static_prompt(first, include_catalog=False)
                + sticker_static_prompt(s)
                + r.tieba.tools.static_prompt()
                + r.bilibili.tools.static_prompt()
                + '\n【主动聊天】你现在有一次自主发起聊天的机会，并没有人刚向你提问。'
                  '结合人设、关系、当前北京时间和真实话题，顺着第一直觉决定是否值得开口；没具体想说的或群友正在呼叫其他机器人/别人时直接沉默（should_reply=false）。'
                  '可分享刚刷到的贴吧/B站趣事见闻、接续尚未解决的话题、或找一个熟人俏皮搭话吐槽。'
                  '先分清是正在接话还是隔了一段时间（或隔夜）重新开聊：如果最近几句和前文隔了很久（topic_gap_seconds较大）或last_human_age_seconds已超过10分钟，闲聊多半已凉；群友说了句没点明对象的短句（如“我也卸载了/没时间玩”）而早先曾同时提过多个不同事物，绝不瞎猜坐实其中某一个，拿不准就直接顺着自然反问一句“你把哪个卸了呀”或直接沉默（should_reply=false）；已结束的旧话题不再逐句点评；隔得久时只有确有新意（例如data.surf_discoveries里刚看过还没在本群聊过的B站有趣视频/帖子）或未完事项才开口，若顺带聊到surf_discoveries里的某个视频可在JSON写"shared_bilibili_id":"对应id"（别像机器播报一样照抄视频标题），不固定打招呼、不当居委会劝架圆场或催促回复。如果上下文含[合并转发聊天记录]，务必看清框内每条原日期、时间和原说话人，几周或几个月前的旧截图或别人互称（哪怕出现熟人或亲密称呼）不是此刻刚说的话，也不是对着你说的，绝不误认或翻旧截图搭话。'
                  '像一个在场的年轻女孩自然抛出一句干脆鲜活的短话，或直接只甩1个贴合当下气氛的原生sticker表情包（有合拍候选且没正经问题要答时，actions只放那1个sticker不补客套文字也非常自然），不当话题主持人或总结员；保留甜软可爱的少女口吻（适度用日常语气词即可，不必句句硬贴尾音），不用句首机械挂“唔/呜”，忙碌人员不可点名或@。'
                  '可按需使用已授权工具（联网搜索、贴吧、B站异步探索、记忆、表情库、图库与生图）；记忆和收藏的source_key只用真实群友消息，不因自己准备开口而改好感度。'
                  '输出JSON：{"should_reply":false,"reason":"供管理员查看的简短依据，不含内部推理",'
                  '"sources":["确切消息key或memory:ID"],"actions":[],"pat_targets":[],"image_request":null,"tieba_request":null,"bilibili_request":null,"shared_bilibili_id":"","tieba_used_meme_ids":[],"search_query":"","fetch_url":"","memory_ops":[],"memory_used_ids":[],"sticker_ops":[],"image_descriptions":[]}。'
                  'actions为有序发送计划，文字为{"type":"text","text":"自然短句","mentions":[]}，表情为{"type":"sticker","id":"目录ID"}；若想俏皮主动拍一拍某个熟人逗TA，可在pat_targets填1个代号如["p1"]（每轮最多1人）；每轮actions最多'+str(s.reply_max_parts)+'个动作，沉默时actions=[]、pat_targets=[]且image_request=null。')
            system += r.autonomy.static_prompt() + r.statistics_tools.prompt()
            # Chat-specific catalog and venue belong after all common rules;
            # changing the roster must not split the reusable system prefix.
            system += r.affection.venue_prompt(first, s)
            if chat.endswith('@chatroom'):system += mention_static_prompt(mentions_enabled)
            system += r.images.tools.catalog_prompt(base_image_catalog)
            recent_ts = [float(row.get('timestamp') or 0) for row in humans[-8:] if row.get('timestamp')]
            max_gap = max((b - a for a, b in zip(recent_ts, recent_ts[1:])), default=0)
            data = {'chat': r.chat_labels.get(chat, chat), 'initiative': s.proactive_level,
                    'do_not_address': sorted(muted),
                    'last_human_age_seconds': round(max(0,now-humans[-1]['timestamp'])),
                    'last_bot_age_seconds': round(max(0,now-self.store.latest_sent(s.account_id,chat)))
                        if self.store.latest_sent(s.account_id,chat) else None}
            if max_gap >= 1800:
                data['topic_gap_seconds'] = round(max_gap)
            surf_discoveries = r.bilibili.unshared_readings(chat, limit=2) if s.bilibili_enabled else []
            surf_discovery_ids = {item['id'] for item in surf_discoveries}
            if surf_discoveries:
                data['surf_discoveries'] = surf_discoveries
            growth_context={}
            growth_prompt=r.growth.prompt(first,humans,growth_context,proactive=True)
            loaded_memory_ids.update(growth_context.get('ids',[]))
            bili_meme_recall = {}
            dynamic = (memory_dynamic + growth_prompt
                       + r.bilibili.meme_prompt(query, bili_meme_recall, chat=chat, proactive=True)
                       + r.images.tools.dynamic_prompt(image_context, base_image_ids)
                       + r.fishtalk.dynamic_prompt(first,humans[-6:])
                       + sticker_dynamic_prompt(choices, r.stickers.details(choices)))
            if choices:
                dynamic+=sticker_usage_prompt(self.store,s,first)
            dynamic += r.autonomy.dynamic_prompt(first, humans[-6:], proactive=True)
            loaded_memory_ids.update(first.get('_interaction_versions', {}))
            source_ids.update('memory:'+str(i) for i in first.get('_interaction_versions', {}))
            if first.get('_reading_offered'):
                loaded_memory_ids.add(first['_reading_offered'])
                source_ids.add('memory:'+str(first['_reading_offered']))
            dynamic += r.affection.dynamic_prompt(first, humans[-6:], s)
            dynamic += mention_dynamic_prompt(mentions,mentions_enabled)
            if pat_options:
                dynamic += '\n可主动轻拍对象（每轮最多填1个代号进pat_targets，不可自造ID）：' + json.dumps(
                    {k: v['name'] for k, v in pat_options.items()}, ensure_ascii=False) + '。'
            if s.anti_formula_guard_enabled:
                dynamic += anti_formula_check(self.store,s.account_id,chat,
                    gentle=r.autonomy.experience.active())['prompt']
            # Offer real recent sticker sources without embedding the full
            # management catalog because an old message mentioned collection.
            dynamic += r.sticker_library.dynamic_prompt(first, [first], s)
            # Separate bounded request: a slow opener cannot occupy the regular reply worker.
            proactive_max_tokens = max(720, s.max_tokens + 480)
            engine = r.engine_factory(api_url=s.api_url, api_key=self.config.api_key(), model=s.model,
                                      api_style=s.api_style,
                                      reasoning_effort=s.reasoning_effort, stream_enabled=s.model_stream_enabled,
                                      max_tokens=proactive_max_tokens, temperature=reply_temperature(s, proactive=True), timeout=25, max_retries=0)
            engine.usage_callback = lambda event: r.token_ledger.record(s.account_id, event)
            tool_messages, image_keys, context_counts = await build_context(
                self.store,r.gateway,s,first,[],r.chat_labels,initiative=True)
            if context_counts.get('pending_images'):
                # Do not start a blind chat about a photo that is still loading.
                # Native reads above never navigate away or scroll old history.
                self.store.metrics(key,{'proactive':True,**context_counts})
                self.finish(identity,'skipped',error='近期图片尚未就绪，本轮不生成看不到图片的回复')
                return True
            if image_keys:
                dynamic+='\n本轮含真实图片，按附件接话，并用image_descriptions记录对应key的可见内容。'
            suffix='\n自主机会资料：'+json.dumps(data,ensure_ascii=False)+dynamic
            content=tool_messages[0]['content']
            if isinstance(content,list):content[0]['text']+=suffix
            else:tool_messages[0]['content']+=suffix
            search_tools = DeepSeekSearch.tools(engine, s) if self.native_tools_disabled_generation != generation else []
            options = dict(system_prompt=system, max_tokens=proactive_max_tokens,
                           response_format={'type':'json_object'}, operation='proactive')
            if search_tools:
                result = await asyncio.wait_for(engine.chat_with_tools(tool_messages, search_tools, **options), timeout=30)
                requested = DeepSeekSearch.native_decision(result)
                raw = json.dumps(requested, ensure_ascii=False) if requested else result.get('content') or ''
                if not raw and str(getattr(engine,'last_error','')).startswith('HTTP 400'):
                    self.native_tools_disabled_generation = generation
                    raw = await asyncio.wait_for(engine.chat(tool_messages, **options), timeout=30)
            else:
                raw = await asyncio.wait_for(engine.chat(tool_messages, **options), timeout=30)
            if not raw or not raw.strip():
                raise ValueError(getattr(engine, 'last_error', '') or '模型未返回内容')
            format_diagnostics = {'proactive': True, **context_counts}
            try:
                raw = await asyncio.wait_for(guarded_chat(engine, tool_messages, diagnostics=format_diagnostics,
                    stage='proactive_decision', initial_raw=raw, **options), timeout=30)
            finally:
                self.store.metrics(key, format_diagnostics)
            decision = parse_json(raw or '')
            if not isinstance(decision, dict) or not isinstance(decision.get('should_reply'), bool):
                raise ValueError('Invalid proactive decision')
            if image_keys:vision_reply(json.dumps(decision,ensure_ascii=False),image_keys,self.store)
            decision = await resolve_tools(r, engine, tool_messages, system, decision, first, humans,
                generation, memory_scope, choices, tieba_evidence, loaded_memory_ids)
            diagnostics={'proactive':True, 'tools':tieba_evidence, **context_counts,
                         'last_human_age_seconds':data['last_human_age_seconds']}
            self.store.metrics(key, diagnostics)
            if decision is None:
                self.finish(identity, 'skipped', error='工具调用期间收到新消息或权限变化，本轮未发送')
                return True
            if not isinstance(decision.get('should_reply'), bool):
                raise ValueError('Invalid proactive tool decision')
            excluded = r.stickers.disabled | r.sticker_library.excluded(s.account_id, chat)
            choices = {key:value for key,value in choices.items() if key not in excluded}
            refs = decision.get('sources') if isinstance(decision.get('sources'), list) else []
            self.finish(identity, 'planning', reason=decision.get('reason') or 'AI 判断本轮是否适合主动聊天',
                        sources=[v for v in refs if isinstance(v, str) and v in source_ids][:12])
            self.last_error = ''
            if not decision['should_reply']:
                if generation == self.config.generation and self.send_allowed(first):
                    r.autonomy.accept(first, humans, decision, proactive=True)
                    r.autonomy.finish_due(first, due_intents)
                    r.learning.offer(first, humans, decision.get('learning_proposals'),
                        recall.get('learning_ids', []), {'delivery':{'code':'silent'}})
                self.finish(identity, 'ignored')
                return True
            actions = apply_mentions(plan_actions(decision, choices, s), mentions, mentions_enabled)
            actions, diagnostics['sticker_pairing'] = pair_sticker(decision, actions, choices, s)
            for action in actions:
                action['quote_local_id'] = 0
            pat_actions = plan_pat_targets(decision, pat_options, s, recent_targets=pat_cooldown_ids)
            if pat_actions:
                diagnostics['pat_planned'] = pat_actions[0]['target_id']
                actions = [*actions, *pat_actions]
            send_ready = True
            if not chat.endswith('@chatroom'):
                send_ready = await self.private_send_slot(first, generation, diagnostics)
                self.store.metrics(key, diagnostics)
            if (not send_ready or generation != self.config.generation or not self.send_allowed(first) or self.blocked()
                    or not self.quota(chat, time.time())):
                self.finish(identity, 'skipped', error='有新消息、权限变化或正在处理其他聊天，本轮未发送')
                return True
            async with r.chat_turn_lock:
                if self.blocked_except_lock() or not self.send_allowed(first):
                    self.finish(identity, 'skipped', error='收到新消息，本轮主动开聊让路')
                    return True
                r.processing_key = key
                r.processing = '自主聊天'
                r.autonomy.accept(first, humans, decision, proactive=True)
                r.autonomy.finish_due(first, due_intents)
                self.store.insert(first, 'generating')
                try:
                    self.finish(identity, 'sending')
                    actions = r.tieba.tools.attach_share(decision, first, actions, 'proactive')
                    if actions:
                        await r.send_plan(first, [], first, actions, {}, generation,
                                          {**diagnostics, 'memory_ids': sorted(loaded_memory_ids)})
                    record = self.store.get(key)
                    sent = record['state'] in ('sent', 'partial') if actions else False
                    if sent and s.memory_enabled:
                        r.memory_hub.mark_used(s.account_id, decision.get('memory_used_ids'), loaded_memory_ids)
                    sent_text = '\n'.join(a['reply'] for a in self.store.actions(key) if a['state'] == 'sent')
                    r.tieba.tools.record_usage(first, decision, tieba_evidence, sent_text)
                    r.bilibili.record_meme_usage(first, bili_meme_recall, sent_text)
                    shared_bili_id = str(decision.get('shared_bilibili_id') or '').strip()
                    if sent and shared_bili_id and shared_bili_id in surf_discovery_ids:
                        r.bilibili.record_share(chat, shared_bili_id)
                    image_result = None
                    if ((not actions or sent) and generation == self.config.generation and self.send_allowed(first, started=sent)):
                        image_result = r.images.tools.execute(decision.get('image_request'), first, [first], image_context)
                    job = image_result.get('job_id', '') if image_result and image_result.get('ok') else ''
                    if sent or job:
                        self.accepted(first, actions)
                    state = record['state'] if actions else 'handled' if job else 'ignored'
                    if job and not actions:
                        self.store.update(key, 'handled', error='主动图片任务已受理，实际结果见图片任务')
                    elif not actions:
                        self.store.update(key, 'ignored', error='没有提交可执行的主动动作')
                    if image_result and not image_result.get('ok'):
                        self.finish(identity, state, error='图片工具未受理：'+image_result['code'])
                    else:
                        self.finish(identity, state, image_job=job, error=record.get('error', '') if actions else '')
                    r.autonomy.experience.settle(first, 'image_queued' if job and not actions else state)
                    r.learning.offer(first, humans, decision.get('learning_proposals'),
                        recall.get('learning_ids', []), {'delivery':{'code':state}})
                except BaseException:
                    record = self.store.get(key)
                    uncertain = bool(self.store.actions(key)) and any(a['state'] in ('sending', 'uncertain') for a in self.store.actions(key))
                    confirmed = any(a['state'] == 'sent' for a in self.store.actions(key))
                    terminal = 'uncertain' if uncertain else 'partial' if confirmed else 'failed'
                    self.store.update(key, terminal, reply=record.get('reply', ''), error='主动任务中断，未自动重发')
                    if confirmed:
                        self.accepted(first, actions)
                    if uncertain:
                        for action in self.store.actions(key):
                            if action['state'] == 'sending':
                                self.store.action_finish(action['id'], 'uncertain', error='主动发送中断，需核对')
                        r.safety.record(first['account'],'send_receipt_pending',
                            '主动发送结果已留档待核实，后续聊天继续，不自动重发',chat=first['chat'],level='info')
                    self.finish(identity, terminal, error='主动任务中断')
                    r.autonomy.experience.settle(first, terminal)
                    raise
                finally:
                    r.tieba.cancel_planned(key)
                    r.processing = ''
                    r.processing_key = ''
            self.last_error = ''
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
                self.last_error = '主动判断超时，本轮未发送'
            elif isinstance(exc, json.JSONDecodeError):
                self.last_error = '模型未返回有效 JSON，本轮未发送'
            elif isinstance(exc, ToolReplyFormatError):
                self.last_error = '主动回复格式恢复失败，本轮未发送'
            elif isinstance(exc, ValueError) and str(exc) == (getattr(engine, 'last_error', '') or '模型未返回内容'):
                self.last_error = str(exc)[:180]
            else:
                self.last_error = type(exc).__name__
            row = self.store.db.execute('SELECT state FROM proactive_runs WHERE id=?', (identity,)).fetchone()
            if row and row['state'] == 'planning':
                self.finish(identity, 'failed', error='主动判断暂不可用：'+self.last_error)
            return False
        finally:
            self.busy = False
            if engine:
                await engine.close()

    def blocked_except_lock(self):
        r = self.runtime
        return (r.processing or self.store.queue() or r.paused_reason or r.safety.held(self.config.settings.account_id)
                or r.moments.lock.locked() or self.ui_busy())

    def snapshot(self):
        account = self.config.settings.account_id
        rows = [dict(row) for row in self.store.db.execute("""SELECT p.*,m.metrics AS tool_metrics FROM proactive_runs p
            LEFT JOIN reply_metrics m ON m.key='proactive:' || p.id
            WHERE p.account=? ORDER BY p.created DESC LIMIT 30""", (account,))]
        for row in rows:
            row['tools'] = json.loads(row.pop('tool_metrics') or '{}').get('tools', {})
            row['sources'] = json.loads(row['sources'])
            if row['state'] in ('sending', 'sent', 'partial', 'uncertain', 'handled'):
                inbox = self.store.get('proactive:'+row['id'])
                if inbox:
                    row['reply'] = inbox['reply']
                    if inbox['state'] in ('sent', 'partial', 'failed', 'uncertain'):
                        row['state'] = inbox['state']
                if row['image_job']:
                    job = self.runtime.images.get(row['image_job'])
                    row['image_state'] = job['state'] if job else 'missing'
        chats = {}
        for chat in self.config.settings.proactive_chat_ids:
            state = self.chat_state(chat)
            if not chat.endswith('@chatroom'):
                last = self.latest_human(chat)
                state['private_idle_at'] = self.private_idle_at(chat, state, last)
                state['waiting_for_reply'] = bool(last and state['waiting_after'] and
                                                 last['timestamp'] <= state['waiting_after'])
            chats[chat] = state
        return {'enabled': self.config.settings.proactive_enabled, 'busy': self.busy, 'last_error': self.last_error,
                'quiet':self.runtime.chat_control.snapshot(),
                'runs': rows, 'chats': chats}

    async def loop(self):
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = type(exc).__name__
            await asyncio.sleep(30)
