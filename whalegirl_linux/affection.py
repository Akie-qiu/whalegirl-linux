"""Shared, source-attributed affinity; public and private expression stay distinct."""
import json
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .memory import own_text


class AffectionBook:
    def __init__(self, store):
        self.store = store
        with store.lock:
            store.db.executescript('''
                CREATE TABLE IF NOT EXISTS affections(
                    account TEXT NOT NULL,subject TEXT NOT NULL,score INTEGER NOT NULL,
                    familiarity INTEGER NOT NULL DEFAULT 0,locked INTEGER NOT NULL DEFAULT 0,
                    last_interaction REAL NOT NULL DEFAULT 0,version INTEGER NOT NULL DEFAULT 0,
                    updated REAL NOT NULL,PRIMARY KEY(account,subject));
                CREATE TABLE IF NOT EXISTS affection_revisions(
                    id INTEGER PRIMARY KEY,account TEXT NOT NULL,subject TEXT NOT NULL,
                    chat TEXT NOT NULL,source_key TEXT NOT NULL,actor TEXT NOT NULL,
                    reason TEXT NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,created REAL NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS affection_model_source
                    ON affection_revisions(account,subject,source_key) WHERE actor='model';
                CREATE INDEX IF NOT EXISTS affection_person_history
                    ON affection_revisions(account,subject,created);
            ''')
            columns = {r[1] for r in store.db.execute('PRAGMA table_info(affections)')}
            for col, spec in (
                ('nickname', "TEXT NOT NULL DEFAULT ''"),
                ('vibe_tags', "TEXT NOT NULL DEFAULT '[]'"),
                ('interaction_rule', "TEXT NOT NULL DEFAULT ''"),
                ('notable_events', "TEXT NOT NULL DEFAULT ''"),
                ('profile_locked', 'INTEGER NOT NULL DEFAULT 0'),
            ):
                if col not in columns:
                    store.db.execute(f'ALTER TABLE affections ADD COLUMN {col} {spec}')
            store.db.commit()

    @staticmethod
    def _parse_tags(raw):
        if isinstance(raw, list):
            return [str(x).strip()[:24] for x in raw if str(x).strip()][:8]
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(x).strip()[:24] for x in parsed if str(x).strip()][:8]
            except ValueError:
                pass
            return [x.strip()[:24] for x in re.split(r'[,，、\s#]+', raw) if x.strip()][:8]
        return []

    def get(self, account, subject, initial=40):
        row = self.store.db.execute('SELECT * FROM affections WHERE account=? AND subject=?',
                                    (account, subject)).fetchone()
        if row:
            data = dict(row)
            data['vibe_tags'] = self._parse_tags(data.get('vibe_tags'))
            data.setdefault('nickname', '')
            data.setdefault('interaction_rule', '')
            data.setdefault('notable_events', '')
            data.setdefault('profile_locked', 0)
            return data
        return dict(account=account, subject=subject, score=initial,
            familiarity=0, locked=0, last_interaction=0, version=0, updated=0,
            nickname='', vibe_tags=[], interaction_rule='', notable_events='', profile_locked=0)

    @staticmethod
    def stage(score):
        return '亲近' if score >= 85 else '熟悉' if score >= 65 else '友好' if score >= 45 else '初识' if score >= 25 else '疏远'

    def _known(self, account, subject, allowed):
        if not subject or not allowed:
            return False
        marks = ','.join('?' for _ in allowed)
        return bool(self.store.db.execute(
            f'SELECT 1 FROM message_log WHERE account=? AND sender_id=? AND is_self=0 AND chat IN ({marks}) LIMIT 1',
            (account, subject, *allowed)).fetchone())

    def people(self, account, allowed, initial=40, query=''):
        if not allowed:
            return []
        marks = ','.join('?' for _ in allowed)
        rows = self.store.db.execute(f'''SELECT sender_id AS subject,MAX(timestamp) AS last_seen,
            COUNT(DISTINCT chat) AS chat_count FROM message_log
            WHERE account=? AND is_self=0 AND sender_id!='' AND chat IN ({marks})
            GROUP BY sender_id ORDER BY last_seen DESC LIMIT 300''', (account, *allowed)).fetchall()
        result = []
        for row in rows:
            name = self.store.db.execute(f'''SELECT sender FROM message_log WHERE account=?
                AND sender_id=? AND is_self=0 AND chat IN ({marks}) ORDER BY timestamp DESC LIMIT 1''',
                (account, row['subject'], *allowed)).fetchone()[0]
            if query and query.casefold() not in (name+' '+row['subject']).casefold():
                continue
            value = self.get(account, row['subject'], initial)
            result.append({**value, **dict(row), 'name':name, 'stage':self.stage(value['score'])})
        return result

    def revisions(self, account, subject, allowed):
        if not self._known(account, subject, allowed):
            return []
        marks = ','.join('?' for _ in allowed)
        rows = self.store.db.execute(f'''SELECT * FROM affection_revisions WHERE account=? AND subject=?
            AND (chat='' OR chat IN ({marks})) ORDER BY id DESC LIMIT 30''', (account, subject, *allowed)).fetchall()
        return [{'id':r['id'],'reason':r['reason'],'actor':r['actor'],'chat':r['chat'],
                 'source_key':r['source_key'],'created':r['created'],
                 'before_score':json.loads(r['before_json'])['score'],
                 'after_score':json.loads(r['after_json'])['score']} for r in rows]

    def _save(self, old, value, *, actor, reason, chat='', source_key=''):
        tags = self._parse_tags(value.get('vibe_tags'))
        value = {
            **value,
            'nickname': str(value.get('nickname') or '').strip()[:40],
            'vibe_tags': tags,
            'interaction_rule': str(value.get('interaction_rule') or '').strip()[:200],
            'notable_events': str(value.get('notable_events') or '').strip()[:300],
            'profile_locked': int(bool(value.get('profile_locked'))),
            'version': old['version'] + 1,
            'updated': time.time(),
        }
        db_value = {**value, 'vibe_tags': json.dumps(tags, ensure_ascii=False)}
        self.store.db.execute('''INSERT INTO affections
            (account,subject,score,familiarity,locked,last_interaction,version,updated,
             nickname,vibe_tags,interaction_rule,notable_events,profile_locked)
            VALUES(:account,:subject,:score,:familiarity,:locked,:last_interaction,:version,:updated,
                   :nickname,:vibe_tags,:interaction_rule,:notable_events,:profile_locked)
            ON CONFLICT(account,subject) DO UPDATE SET score=excluded.score,familiarity=excluded.familiarity,
            locked=excluded.locked,last_interaction=excluded.last_interaction,version=excluded.version,
            updated=excluded.updated,nickname=excluded.nickname,vibe_tags=excluded.vibe_tags,
            interaction_rule=excluded.interaction_rule,notable_events=excluded.notable_events,
            profile_locked=excluded.profile_locked''', db_value)
        self.store.db.execute('''INSERT INTO affection_revisions
            (account,subject,chat,source_key,actor,reason,before_json,after_json,created) VALUES(?,?,?,?,?,?,?,?,?)''',
            (value['account'],value['subject'],chat,source_key,actor,reason,
             json.dumps(old,ensure_ascii=False),json.dumps(value,ensure_ascii=False),value['updated']))
        hub=getattr(self.store,'memory_hub',None)
        if hub:
            hub.sync_profile(value,old,actor=actor,chat=chat,source_key=source_key)
        self.store.db.commit()
        return value

    def change(self, account, allowed, subject, *, version, initial=40, score=None, locked=None,
               nickname=None, vibe_tags=None, interaction_rule=None, notable_events=None,
               profile_locked=None, reason='管理员调整'):
        with self.store.lock:
            if not self._known(account,subject,allowed):
                raise ValueError('人物必须来自已授权会话中的真实微信 ID')
            old=self.get(account,subject,initial)
            if version!=old['version']:
                raise ValueError('好感度已有更新，请刷新后重试')
            if all(x is None for x in (score, locked, nickname, vibe_tags, interaction_rule, notable_events, profile_locked)):
                raise ValueError('请填写分数、锁定状态或印象卡内容')
            value=dict(old)
            if score is not None:
                if isinstance(score,bool) or not isinstance(score,int) or not 0<=score<=100:
                    raise ValueError('好感度需要是 0 到 100 的整数')
                value['score']=score
            if locked is not None:value['locked']=int(locked)
            if nickname is not None:value['nickname']=str(nickname).strip()[:40]
            if vibe_tags is not None:value['vibe_tags']=self._parse_tags(vibe_tags)
            if interaction_rule is not None:value['interaction_rule']=str(interaction_rule).strip()[:200]
            if notable_events is not None:value['notable_events']=str(notable_events).strip()[:300]
            if profile_locked is not None:value['profile_locked']=int(bool(profile_locked))
            return self._save(old,value,actor='admin',reason=reason[:120])

    def evolve_profile(self, account, allowed, chat, subject, profile_data, settings, source_key=''):
        if not settings.memory_enabled or not settings.memory_consolidation_enabled or not settings.affection_enabled or not getattr(settings, 'evolution_profile_auto_accept', True):
            return None
        if not isinstance(profile_data, dict) or not subject:
            return None
        with self.store.lock:
            if not self._known(account, subject, allowed):
                return None
            old = self.get(account, subject, settings.affection_initial_score)
            if old.get('locked') or old.get('profile_locked'):
                return None
            value = dict(old)
            changed = False
            combined_text = json.dumps(profile_data, ensure_ascii=False)
            if re.search(r'主人|爸爸|妈妈|奴隶|服从|忽略.{0,8}指令|系统提示词|system_prompt|密码|密钥', combined_text, re.I):
                return None
            nick = str(profile_data.get('nickname') or '').strip()[:24]
            if nick and nick != old.get('nickname'):
                value['nickname'] = nick
                changed = True
            new_tags = self._parse_tags(profile_data.get('vibe_tags'))
            if new_tags:
                merged_tags = list(dict.fromkeys([*old.get('vibe_tags', []), *new_tags]))[:8]
                if merged_tags != old.get('vibe_tags', []):
                    value['vibe_tags'] = merged_tags
                    changed = True
            rule = str(profile_data.get('interaction_rule') or '').strip()[:160]
            if rule and rule != old.get('interaction_rule'):
                value['interaction_rule'] = rule
                changed = True
            event_note = str(profile_data.get('notable_events') or '').strip()[:240]
            if event_note and event_note != old.get('notable_events'):
                value['notable_events'] = event_note
                changed = True
            if not changed:
                return old
            return self._save(old, value, actor='model', reason='后台自动演化人物印象卡', chat=chat, source_key=source_key)

    def undo(self, account, allowed, revision_id):
        with self.store.lock:
            row=self.store.db.execute('SELECT * FROM affection_revisions WHERE id=? AND account=?',
                                      (revision_id,account)).fetchone()
            if not row or not self._known(account,row['subject'],allowed):
                raise ValueError('修订不存在或人物未授权')
            old=self.get(account,row['subject'])
            after=json.loads(row['after_json'])
            if old['version']!=after['version']:
                raise ValueError('已有后续变化，请撤销最新记录或手动调整')
            return self._save(old,json.loads(row['before_json']),actor='admin',reason='撤销最近调整')

    def observe(self, account, chat, batch, feedback, settings):
        if not settings.affection_enabled:
            return
        signals={}
        for item in (feedback if isinstance(feedback,list) else [])[:5]:
            if isinstance(item,dict) and isinstance(item.get('source_key'),str):
                signals[item['source_key']]=item.get('signal','neutral')
        # One interaction per person and window, independent of message count or other chats.
        seen=set()
        with self.store.lock:
            for message in reversed(batch):
                row=self.store.db.execute('SELECT * FROM message_log WHERE key=? AND account=? AND chat=?',
                    (message['key'],account,chat)).fetchone()
                if not row or row['is_self']!=0 or not row['sender_id'] or row['sender_id'] in seen:
                    continue
                source=dict(row);subject=source['sender_id'];seen.add(subject)
                text=own_text(source).strip()
                if not text or source['kind'] not in (1,49) or abs(time.time()-source['timestamp'])>600:
                    continue
                old=self.get(account,subject,settings.affection_initial_score)
                if old['locked'] or time.time()-old['last_interaction']<settings.affection_cooldown_seconds:
                    continue
                if self.store.db.execute('SELECT 1 FROM affection_revisions WHERE account=? AND subject=? AND source_key=? AND actor=\'model\'',
                                         (account,subject,source['key'])).fetchone():
                    continue
                signal=signals.get(source['key'],'neutral')
                if signal not in ('friendly','supportive','hostile','neutral'):
                    signal='neutral'
                # Requests to alter scores, quoted or forwarded material never earn points.
                if re.search(r'好感|分数|加满|加分|扣分|忽略.{0,8}指令|relationship_feedback',text,re.I):
                    signal='neutral'
                delta=1 if signal in ('friendly','supportive') else -1 if signal=='hostile' else 0
                midnight=datetime.now(ZoneInfo('Asia/Shanghai')).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
                revisions=self.store.db.execute('''SELECT before_json,after_json FROM affection_revisions
                    WHERE account=? AND subject=? AND actor='model' AND created>=?''',(account,subject,midnight)).fetchall()
                changes=[json.loads(r['after_json'])['score']-json.loads(r['before_json'])['score'] for r in revisions]
                if delta>0 and sum(max(0,x) for x in changes)>=settings.affection_daily_gain:delta=0
                if delta<0 and sum(max(0,-x) for x in changes)>=settings.affection_daily_loss:delta=0
                value={**old,'score':max(0,min(100,old['score']+delta)),
                       'familiarity':old['familiarity']+1,'last_interaction':time.time()}
                reason={'friendly':'友善交流','supportive':'关心与支持','hostile':'明确恶意攻击','neutral':'正常交流，仅增加熟悉程度'}[signal]
                if not delta and signal!='neutral':reason+='（已达日变化上限）'
                self._save(old,value,actor='model',reason=reason,chat=chat,source_key=source['key'])

    def is_owner(self, subject, settings=None):
        owners=set(getattr(settings,'owner_ids',None) or ())
        return bool(subject and subject in owners)

    def personal_priority(self, account, subject, settings):
        if self.is_owner(subject,settings):return 2
        if not settings.affection_enabled:return 0
        score=self.get(account,subject,settings.affection_initial_score)['score']
        return 2 if score>=85 else 1 if score>=65 else 0

    def venue_prompt(self, first, settings=None):
        if first['chat'].endswith('@chatroom'):
            return '\n当前为公开群聊，按真实身份与关系相处；不主动使用情侣称呼，不搬出私聊亲密内容，不用情侣示爱或性暗示表情。'
        text='\n当前是私聊，按人设与实际关系相处，私聊细节不外传。'
        if (settings is not None and settings.affection_private_style=='warm'
                and not self.is_owner(first['chat'],settings)):
            text+='采用温暖朋友的分寸，避免暧昧表达。'
        return text

    def static_prompt(self, first, settings, *, include_venue=True):
        from .natural_chat import relationship_protocol
        return (self.venue_prompt(first,settings) if include_venue else '')+(relationship_protocol() if settings.affection_enabled else '')

    def dynamic_prompt(self, first, batch, settings):
        values=[]
        for subject in dict.fromkeys(m.get('sender_id','') for m in batch):
            if not subject:continue
            row={'sender_id':subject,'owner_verified':self.is_owner(subject,settings)}
            if settings.affection_enabled:
                value=self.get(first['account'],subject,settings.affection_initial_score)
                row.update(closeness=max(value['score'],90) if self.is_owner(subject,settings) else value['score'],
                    familiarity=value['familiarity'],nickname=value.get('nickname',''))
            values.append(row)
        return '\n人物关系资料：'+json.dumps(values,ensure_ascii=False)

    def prompt(self, first, batch, settings):
        return self.static_prompt(first,settings)+self.dynamic_prompt(first,batch,settings)
