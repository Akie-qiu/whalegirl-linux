"""One source of truth for factual, episodic and learned memories.

The existing memories/revisions tables remain the canonical store. Legacy learning
and profile tables are compatibility projections; all interactive recall uses this
repository. Migrations are additive and preserve old rows and IDs.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time


TIERS = {'core', 'stable', 'ordinary', 'cold'}
KINDS = {'fact', 'preference', 'impression', 'relationship', 'event', 'joke',
         'self_stance', 'shared_moment', 'chat_style', 'meme_usage', 'tool_learning',
         'expression', 'persona', 'bot_alias', 'slang', 'nickname', 'interaction_rule'}
STATES = {'active', 'pending', 'superseded', 'forgotten', 'expired', 'rejected', 'blocked'}
LEARNING_STATES = {'accepted': 'active', 'pending': 'pending', 'revoked': 'forgotten',
                   'rejected': 'rejected', 'expired': 'expired', 'blocked': 'blocked'}
ROLE_KINDS = {'self_stance', 'shared_moment', 'persona', 'expression', 'tool_learning',
              'interaction_rule', 'impression', 'chat_style', 'nickname'}
FACT_KINDS = {'fact', 'preference'}


def normalized(text):
    return re.sub(r'[\s，。！？、,.!?；;：:]+', '', str(text).casefold())


def token_estimate(text):
    # Conservative local estimate, without a model request or tokenizer dependency.
    cjk = len(re.findall(r'[\u3400-\u9fff]', text))
    return max(1, math.ceil(cjk + (len(text) - cjk) / 3))


def default_tier(kind):
    return 'stable' if kind in FACT_KINDS | {'nickname', 'interaction_rule', 'chat_style', 'bot_alias'} else 'ordinary'


class DurableMemoryQueue:
    """Small persistent outbox; a restart does not lose learning proposals."""
    def __init__(self, hub):
        self.hub = hub
        self.current = None

    def qsize(self):
        with self.hub.store.lock:
            return self.hub.db.execute("SELECT count(*) FROM memory_work_queue WHERE state='queued'").fetchone()[0]

    def empty(self):
        return self.qsize() == 0

    def put_nowait(self, payload):
        raw = json.dumps(payload, ensure_ascii=False)
        key = hashlib.sha256(raw.encode()).hexdigest()
        with self.hub.store.lock, self.hub.db:
            if self.hub.db.execute('SELECT 1 FROM memory_work_queue WHERE fingerprint=?', (key,)).fetchone():
                return
            if self.qsize() >= 64:
                raise asyncio.QueueFull
            self.hub.db.execute('INSERT INTO memory_work_queue(fingerprint,payload,state,created) VALUES(?,?,?,?)',
                                (key, raw, 'queued', time.time()))

    def get_nowait(self):
        with self.hub.store.lock, self.hub.db:
            row = self.hub.db.execute("SELECT * FROM memory_work_queue WHERE state='queued' ORDER BY id LIMIT 1").fetchone()
            if not row:
                raise asyncio.QueueEmpty
            self.current = row['id']
            self.hub.db.execute("UPDATE memory_work_queue SET state='processing' WHERE id=?", (self.current,))
            return json.loads(row['payload'])

    def task_done(self):
        if self.current is not None:
            with self.hub.store.lock, self.hub.db:
                self.hub.db.execute("UPDATE memory_work_queue SET state='done' WHERE id=?", (self.current,))
            self.current = None

    def retry(self):
        if self.current is not None:
            with self.hub.store.lock, self.hub.db:
                self.hub.db.execute("""UPDATE memory_work_queue SET attempts=attempts+1,
                    state=CASE WHEN attempts>=2 THEN 'failed' ELSE 'queued' END WHERE id=?""", (self.current,))
            self.current = None


class MemoryHub:
    def __init__(self, book):
        self.book, self.store, self.db = book, book.store, book.store.db
        self.config = None
        with self.store.lock:
            columns = {r[1] for r in self.db.execute('PRAGMA table_info(memories)')}
            specs = {
                'tier': "TEXT NOT NULL DEFAULT ''", 'status': "TEXT NOT NULL DEFAULT ''",
                'confidence': 'REAL NOT NULL DEFAULT 1', 'role_id': "TEXT NOT NULL DEFAULT ''",
                'property_key': "TEXT NOT NULL DEFAULT ''", 'valid_from': 'REAL NOT NULL DEFAULT 0',
                'valid_until': 'REAL NOT NULL DEFAULT 0', 'last_recalled': 'REAL NOT NULL DEFAULT 0',
                'recall_count': 'INTEGER NOT NULL DEFAULT 0', 'last_used': 'REAL NOT NULL DEFAULT 0',
                'use_count': 'INTEGER NOT NULL DEFAULT 0', 'locked': 'INTEGER NOT NULL DEFAULT 0',
                'venue': "TEXT NOT NULL DEFAULT 'any'", 'source_class': "TEXT NOT NULL DEFAULT 'memory'",
                'projection_json': "TEXT NOT NULL DEFAULT '{}'",
            }
            for key, spec in specs.items():
                if key not in columns:
                    self.db.execute(f'ALTER TABLE memories ADD COLUMN {key} {spec}')
            self.db.executescript('''
                CREATE INDEX IF NOT EXISTS memory_tiers ON memories(account,chat,status,tier,subject);
                CREATE TABLE IF NOT EXISTS memory_evidence(
                    id INTEGER PRIMARY KEY,memory_id INTEGER NOT NULL,account TEXT NOT NULL,
                    source_key TEXT NOT NULL,source_type TEXT NOT NULL,author_id TEXT NOT NULL DEFAULT '',
                    occurred REAL NOT NULL,excerpt TEXT NOT NULL DEFAULT '',receipt TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(memory_id,source_key,source_type));
                CREATE INDEX IF NOT EXISTS memory_evidence_source ON memory_evidence(account,source_key);
                CREATE TABLE IF NOT EXISTS memory_origins(
                    account TEXT NOT NULL,source TEXT NOT NULL,origin_id TEXT NOT NULL,
                    memory_id INTEGER NOT NULL,origin_version INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(account,source,origin_id));
                CREATE TABLE IF NOT EXISTS memory_dependencies(
                    parent_id INTEGER NOT NULL,child_id INTEGER NOT NULL,PRIMARY KEY(parent_id,child_id));
                CREATE TABLE IF NOT EXISTS memory_suppressions(
                    memory_id INTEGER NOT NULL,account TEXT NOT NULL,source_key TEXT NOT NULL,
                    signature TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(memory_id,source_key,signature));
                CREATE TABLE IF NOT EXISTS memory_work_queue(
                    id INTEGER PRIMARY KEY,fingerprint TEXT NOT NULL UNIQUE,payload TEXT NOT NULL,
                    state TEXT NOT NULL,created REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS memory_hub_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            ''')
            if 'attempts' not in {r[1] for r in self.db.execute('PRAGMA table_info(memory_work_queue)')}:
                self.db.execute('ALTER TABLE memory_work_queue ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0')
            self.db.execute("UPDATE memories SET tier=CASE WHEN kind IN ('fact','preference') THEN 'stable' ELSE 'ordinary' END WHERE tier=''")
            self.db.execute("UPDATE memories SET status=CASE WHEN archived=1 THEN 'forgotten' ELSE 'active' END WHERE status=''")
            self.db.execute("UPDATE memory_work_queue SET state='queued' WHERE state='processing'")
            self.db.commit()

    @property
    def settings(self):
        return self.config.settings if self.config else None

    def enabled(self):
        return not self.settings or self.settings.memory_enabled

    def role(self):
        return (self.settings.active_persona_id or 'default') if self.settings else 'default'

    def source(self, account, key):
        row = self.db.execute('SELECT * FROM message_log WHERE account=? AND key=?', (account, key)).fetchone()
        return dict(row) if row else None

    def evidence(self, row, key, source_type='message', excerpt='', receipt=None):
        if not key:
            return
        source = self.source(row['account'], key)
        self.db.execute('''INSERT OR IGNORE INTO memory_evidence
            (memory_id,account,source_key,source_type,author_id,occurred,excerpt,receipt)
            VALUES(?,?,?,?,?,?,?,?)''', (row['id'], row['account'], key, source_type,
            source['sender_id'] if source else '', source['timestamp'] if source else row['created'],
            excerpt[:500], json.dumps(receipt or {}, ensure_ascii=False)))

    def prepare(self, value, old, data, actor):
        tier = data.get('tier', (old or {}).get('tier') or default_tier(value['kind']))
        if tier not in TIERS:
            raise ValueError('记忆等级无效')
        if actor != 'admin' and (tier == 'core' or (old or {}).get('locked') or data.get('locked')):
            raise ValueError('核心置顶和已锁定记忆由管理员维护')
        state = data.get('status', (old or {}).get('status', 'active'))
        if value.get('archived'):
            state = 'forgotten'
        elif data.get('op') == 'restore' and 'status' not in data:
            state = 'active'
            if value.get('expires_at') and value['expires_at']<=time.time():value['expires_at']=0
        if state not in STATES:
            raise ValueError('记忆状态无效')
        if actor != 'admin' and data.get('op') == 'update' and (old or {}).get('status') != 'active':
            raise ValueError('不能自动恢复已停用的记忆')
        prop = str(data.get('property_key', (old or {}).get('property_key', '')) or '').strip()
        if len(prop) > 80:
            raise ValueError('事实属性过长')
        if actor!='admin' and prop and self.db.execute('''SELECT 1 FROM memories WHERE account=? AND subject=?
            AND property_key=? AND status='active' AND (locked=1 OR tier='core') AND id!=?
            AND (role_id='' OR role_id=?) AND (scope='profile' OR chat=?)''',
            (value['account'],value['subject'],prop,(old or {}).get('id',0),self.role(),value['chat'])).fetchone():
            raise ValueError('该属性已由管理员置顶或锁定')
        venue = data.get('venue', (old or {}).get('venue', 'any'))
        if venue not in ('any', 'group', 'private'):
            raise ValueError('适用场合无效')
        if actor != 'admin' and not old and value['kind'] in ROLE_KINDS and not value['chat'].endswith('@chatroom'):
            venue = 'private'
        role = (old or {}).get('role_id') or (self.role() if value['kind'] in ROLE_KINDS else '')
        value.update(tier=tier, status=state, property_key=prop, venue=venue, role_id=role,
                     locked=int(bool(data.get('locked', (old or {}).get('locked', 0)))),
                     valid_from=(old or {}).get('valid_from') or value.get('created') or time.time(),
                     valid_until=(old or {}).get('valid_until', 0), confidence=(old or {}).get('confidence', 1))
        return value

    def written(self, value, old, data, actor):
        self.db.execute('''UPDATE memories SET tier=:tier,status=:status,property_key=:property_key,
            venue=:venue,role_id=:role_id,locked=:locked,valid_from=:valid_from,
            valid_until=:valid_until,confidence=:confidence WHERE id=:id''', value)
        self.evidence(value, data.get('source_key', ''), 'admin' if actor == 'admin' else 'message')
        if not old or old['content'] != value['content']:
            # Explicit property keys allow temporal replacement without guessing
            # whether two semantically similar sentences contradict each other.
            if value['property_key'] and value['status'] == 'active':
                previous = self.db.execute('''SELECT * FROM memories WHERE account=? AND subject=?
                    AND property_key=? AND scope=? AND role_id=? AND status='active' AND id!=?
                    AND (scope='profile' OR chat=?)''', (value['account'], value['subject'], value['property_key'],
                    value['scope'], value['role_id'], value['id'], value['chat'])).fetchall()
                for r in previous:
                    if r['locked'] and actor != 'admin':
                        continue
                    before = dict(r)
                    self.db.execute("UPDATE memories SET status='superseded',valid_until=?,version=version+1 WHERE id=?",
                                    (time.time(), r['id']))
                    after = dict(self.db.execute('SELECT * FROM memories WHERE id=?', (r['id'],)).fetchone())
                    self.book._revision(value['account'], after, before, actor, 'supersede')
                    self.suppress(after)
        if value['status'] in ('forgotten', 'superseded', 'blocked', 'rejected'):
            self.suppress(value)
        elif data.get('op') == 'restore':
            self.db.execute('UPDATE memory_suppressions SET active=0 WHERE memory_id=?', (value['id'],))
        elif old and old['content'] != value['content']:
            self.suppress(old)
        self.project_learning(value)

    def suppress(self, row, visited=None):
        visited = set() if visited is None else visited
        if row['id'] in visited:
            return
        visited.add(row['id'])
        keys = {r[0] for r in self.db.execute('SELECT source_key FROM memory_evidence WHERE memory_id=?', (row['id'],))}
        keys.add(row.get('source_key', ''))
        signature = hashlib.sha256(json.dumps([row['account'], row['chat'], row['subject'], normalized(row['content'])], ensure_ascii=False).encode()).hexdigest()
        for key in keys:
            self.db.execute('''INSERT INTO memory_suppressions(memory_id,account,source_key,signature,active)
                VALUES(?,?,?,?,1) ON CONFLICT(memory_id,source_key,signature) DO UPDATE SET active=1''',
                (row['id'], row['account'], key, signature))
            source = self.source(row['account'], key) if key else None
            chat = source['chat'] if source else row['chat']
            self.db.execute("UPDATE chat_digests SET summary='' WHERE account=? AND chat=?", (row['account'], chat))
        for link in self.db.execute('SELECT child_id FROM memory_dependencies WHERE parent_id=?', (row['id'],)).fetchall():
            child = self.db.execute('SELECT * FROM memories WHERE id=?', (link[0],)).fetchone()
            if not child or child['status'] != 'active':
                continue
            remaining = self.db.execute('''SELECT count(*) FROM memory_dependencies d JOIN memories m
                ON m.id=d.parent_id WHERE d.child_id=? AND m.status='active' AND m.id!=?''', (child['id'], row['id'])).fetchone()[0]
            if not remaining:
                before = dict(child)
                self.db.execute("UPDATE memories SET status='forgotten',archived=1,version=version+1 WHERE id=?", (child['id'],))
                after = dict(self.db.execute('SELECT * FROM memories WHERE id=?', (child['id'],)).fetchone())
                self.book._revision(row['account'], after, before, 'system', 'source_withdrawn')
                self.suppress(after, visited)
                self.project_learning(after)

    def blocked(self, account, chat, subject, content, source_key='', kind=''):
        signature = hashlib.sha256(json.dumps([account, chat, subject, normalized(content)], ensure_ascii=False).encode()).hexdigest()
        return bool(self.db.execute('''SELECT 1 FROM memory_suppressions s JOIN memories m ON m.id=s.memory_id
            WHERE s.account=? AND s.active=1 AND (s.signature=? OR
            (s.source_key!='' AND s.source_key=? AND (?='' OR m.kind=?))) LIMIT 1''',
            (account, signature, source_key,kind,kind)).fetchone())

    def suppressed_sources(self, account, allowed):
        if not allowed:
            return []
        marks = ','.join('?' for _ in allowed)
        return [r[0] for r in self.db.execute(f'''SELECT DISTINCT s.source_key FROM memory_suppressions s
            JOIN memories m ON m.id=s.memory_id WHERE s.account=? AND s.active=1 AND s.source_key!=''
            AND m.chat IN ({marks})''', (account, *allowed))]

    def sync_learning(self, row, old=None, operation='import', actor='migration'):
        """Called inside the legacy writer's transaction, never a second brain."""
        origin = self.db.execute("SELECT * FROM memory_origins WHERE account=? AND source='learning' AND origin_id=?",
                                 (row['account'], str(row['id']))).fetchone()
        if origin and origin['origin_version'] == row['version']:
            return
        canonical = self.db.execute('SELECT * FROM memories WHERE id=?', (origin['memory_id'],)).fetchone() if origin else None
        state = LEARNING_STATES[row['state']]
        if canonical and canonical['status'] in ('forgotten', 'blocked', 'superseded') and operation not in ('accept', 'revise'):
            return
        if not canonical:
            canonical = self.db.execute('''SELECT * FROM memories WHERE account=? AND chat=? AND subject=?
                AND kind=? AND scope=? AND content=? AND status NOT IN ('forgotten','superseded','blocked')
                AND (role_id='' OR role_id=?) ORDER BY id LIMIT 1''',
                (row['account'], row['chat'], row['subject'], row['kind'], row['scope'], row['content'],self.role())).fetchone()
        if canonical and actor not in ('admin','migration') and (canonical['locked'] or canonical['tier']=='core'):
            self.db.execute('''INSERT INTO memory_origins VALUES(?,'learning',?,?,?)
                ON CONFLICT(account,source,origin_id) DO UPDATE SET origin_version=excluded.origin_version''',
                (row['account'],str(row['id']),canonical['id'],row['version']))
            self.evidence(dict(canonical),row['source_key'],'learning',row.get('evidence',''))
            self.project_learning(dict(canonical))
            return
        before = dict(canonical) if canonical else None
        if canonical:
            identity = canonical['id']
            # A pending duplicate cannot deactivate an independently confirmed fact.
            if state == 'pending' and canonical['status'] == 'active' and not origin:
                state = 'active'
            self.db.execute('''UPDATE memories SET content=?,status=?,archived=?,expires_at=?,confidence=?,
                version=version+1,updated=? WHERE id=?''', (row['content'], state, int(state == 'forgotten'),
                row['expires_at'], row['confidence'], row['updated'], identity))
        else:
            cur = self.db.execute('''INSERT INTO memories(account,chat,subject,kind,scope,content,source_key,
                actor,version,archived,created,updated,expires_at,triggers,salience,tier,status,confidence,
                role_id,valid_from,source_class,venue) VALUES(?,?,?,?,?,?,?,'model',1,?,?,?,?,?,3,?,?,?,?,?,'learning',?)''',
                (row['account'], row['chat'], row['subject'], row['kind'], row['scope'], row['content'], row['source_key'],
                 int(state == 'forgotten'), row['created'], row['updated'], row['expires_at'], row.get('alias_target', ''),
                 default_tier(row['kind']), state, row['confidence'], self.role() if row['kind'] in ROLE_KINDS else '',
                 row['created'], 'private' if row['kind'] in ROLE_KINDS and not row['chat'].endswith('@chatroom') else 'any'))
            identity = cur.lastrowid
        value = dict(self.db.execute('SELECT * FROM memories WHERE id=?', (identity,)).fetchone())
        self.db.execute('''INSERT INTO memory_origins(account,source,origin_id,memory_id,origin_version)
            VALUES(?,'learning',?,?,?) ON CONFLICT(account,source,origin_id)
            DO UPDATE SET memory_id=excluded.memory_id,origin_version=excluded.origin_version''',
            (row['account'], str(row['id']), identity, row['version']))
        self.evidence(value, row['source_key'], 'learning', row.get('evidence', ''), json.loads(row.get('receipt_json') or '{}'))
        self.book._revision(row['account'], value, before, 'model' if operation != 'import' else 'migration', 'learning_' + operation)
        if state in ('forgotten', 'blocked', 'rejected'):
            self.suppress(value)
        elif before and before['content'] != value['content']:
            self.suppress(before)
        if operation == 'accept':
            self.db.execute('UPDATE memory_suppressions SET active=0 WHERE memory_id=?', (identity,))

    def project_learning(self, row):
        mappings = self.db.execute("SELECT * FROM memory_origins WHERE account=? AND source='learning' AND memory_id=?",
                                   (row['account'], row['id'])).fetchall()
        reverse = {'active': 'accepted', 'forgotten': 'revoked', 'superseded': 'revoked',
                   'pending': 'pending', 'expired': 'expired', 'rejected': 'rejected', 'blocked': 'blocked'}
        for mapping in mappings:
            old = self.db.execute('SELECT * FROM learning_proposals WHERE account=? AND id=?',
                                  (row['account'], mapping['origin_id'])).fetchone()
            if not old:
                continue
            value = {**dict(old), 'content': row['content'], 'state': reverse[row['status']],
                     'expires_at': row['expires_at'], 'updated': row['updated'], 'version': old['version'] + 1}
            self.db.execute('''UPDATE learning_proposals SET content=:content,state=:state,expires_at=:expires_at,
                version=:version,updated=:updated WHERE id=:id''', value)
            self.db.execute('''INSERT INTO learning_revisions(proposal_id,account,actor,operation,before_json,after_json,created)
                VALUES(?,?,'hub','unified_edit',?,?,?)''', (old['id'], row['account'], json.dumps(dict(old), ensure_ascii=False),
                json.dumps(value, ensure_ascii=False), time.time()))
            self.db.execute("UPDATE memory_origins SET origin_version=? WHERE account=? AND source='learning' AND origin_id=?",
                            (value['version'], row['account'], mapping['origin_id']))

    def learning_evidence(self,proposal,sources):
        origin=self.db.execute("SELECT memory_id FROM memory_origins WHERE account=? AND source='learning' AND origin_id=?",
                               (proposal['account'],str(proposal['id']))).fetchone()
        if not origin:return
        row=dict(self.db.execute('SELECT * FROM memories WHERE id=?',(origin[0],)).fetchone())
        for source in sources:self.evidence(row,source['key'],'learning')
        count=self.db.execute("SELECT count(DISTINCT author_id) FROM memory_evidence WHERE memory_id=? AND author_id!='' AND source_type IN ('message','learning','legacy')",(row['id'],)).fetchone()[0]
        if row['status']=='active' and row['tier']=='ordinary' and row['kind'] in ('slang','meme_usage') and count>=2:
            self.db.execute("UPDATE memories SET tier='stable',version=version+1 WHERE id=?",(row['id'],))
            value=dict(self.db.execute('SELECT * FROM memories WHERE id=?',(row['id'],)).fetchone())
            self.book._revision(row['account'],value,row,'system','evidence_promote')

    def migrate(self):
        with self.store.lock, self.db:
            tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'learning_proposals' in tables:
                for row in self.db.execute('SELECT * FROM learning_proposals ORDER BY id').fetchall():
                    self.sync_learning(dict(row))
            if not self.db.execute("SELECT 1 FROM memory_hub_meta WHERE key='initial_sources_v1'").fetchone():
                for source in self.db.execute('SELECT * FROM memories').fetchall():
                    row = dict(source)
                    if row['kind'] in ROLE_KINDS and not row['role_id']:
                        self.db.execute('UPDATE memories SET role_id=? WHERE id=?',(self.role(),row['id']))
                    if not row['valid_from']:
                        self.db.execute('UPDATE memories SET valid_from=created WHERE id=?',(row['id'],))
                    self.evidence(row, row['source_key'], 'legacy')
                    if row['status'] == 'forgotten':
                        self.suppress(row)
                self.db.execute("INSERT INTO memory_hub_meta VALUES('initial_sources_v1',?)", (str(time.time()),))

    def sync_profile(self, value, old=None, *, actor='migration', chat='', source_key=''):
        """Textual person cards are derived records; numeric affinity stays separate."""
        if actor == 'model' and (not self.enabled() or not self.settings.memory_consolidation_enabled):
            return
        source = self.source(value['account'], source_key) if source_key else None
        if actor == 'model' and (not source or source['is_self'] or source['sender_id'] != value['subject']):
            return
        chat = chat or (source['chat'] if source else '')
        if not chat:
            allowed = self.settings.allowed_chat_ids if self.settings else []
            marks = ','.join('?' for _ in allowed) or "''"
            message = self.db.execute(f'''SELECT chat FROM message_log WHERE account=? AND sender_id=?
                AND chat IN ({marks}) ORDER BY timestamp DESC LIMIT 1''',
                (value['account'], value['subject'], *allowed)).fetchone()
            chat = message['chat'] if message else ''
        if not chat:
            return
        labels = {'nickname': ('nickname', '称呼'), 'vibe_tags': ('impression', '相处印象'),
                  'interaction_rule': ('interaction_rule', '相处方式'), 'notable_events': ('event', '共同经历')}
        for field, (kind, label) in labels.items():
            content = value.get(field, '')
            if old and content == old.get(field):
                continue
            if isinstance(content, list):
                content = '、'.join(content)
            elif field == 'vibe_tags':
                try:
                    parsed = json.loads(content or '[]')
                    if isinstance(parsed, list):content = '、'.join(parsed)
                except ValueError:
                    pass
            origin_id = self.role() + '/' + value['subject'] + '/' + field + '/' + chat
            origin = self.db.execute("SELECT * FROM memory_origins WHERE account=? AND source='profile' AND origin_id=?",
                                     (value['account'], origin_id)).fetchone()
            if not content:
                if origin:
                    row = self.book.get(value['account'], origin['memory_id'], [chat])
                    if row and row['status'] == 'active':
                        self.book.change(value['account'], [chat], {'op':'archive', 'id':row['id'], 'version':row['version']})
                continue
            body = label + '：' + str(content)
            if actor != 'admin' and self.blocked(value['account'], chat, value['subject'], body, source_key):
                continue
            before = dict(self.db.execute('SELECT * FROM memories WHERE id=?', (origin['memory_id'],)).fetchone()) if origin else None
            if before and before['content'] == body:
                continue
            if before and before['status'] in ('forgotten', 'blocked') and actor != 'admin':
                continue
            scope = 'profile' if actor == 'admin' and kind != 'event' else 'chat'
            venue = 'any' if actor == 'admin' else ('group' if chat.endswith('@chatroom') else 'private')
            state = 'pending' if actor == 'migration' and not source else 'active'
            if before:
                identity = before['id']
                self.db.execute('''UPDATE memories SET content=?,status=?,archived=0,version=version+1,
                    updated=? WHERE id=?''', (body, state, time.time(), identity))
            else:
                cur = self.db.execute('''INSERT INTO memories(account,chat,subject,kind,scope,content,source_key,
                    actor,version,created,updated,tier,status,role_id,venue,source_class)
                    VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,'profile')''',
                    (value['account'], chat, value['subject'], kind, scope, body, source_key, actor,
                     value['updated'], value['updated'], default_tier(kind), state, self.role(), venue))
                identity = cur.lastrowid
            row = dict(self.db.execute('SELECT * FROM memories WHERE id=?', (identity,)).fetchone())
            self.evidence(row, source_key, 'profile')
            self.db.execute('''INSERT INTO memory_origins VALUES(?,'profile',?,?,?) ON CONFLICT(account,source,origin_id)
                DO UPDATE SET origin_version=excluded.origin_version''', (value['account'], origin_id, identity, value['version']))
            self.book._revision(value['account'], row, before, actor, 'profile_update')
            if before:
                self.suppress(before)

    def migrate_profiles(self):
        with self.store.lock, self.db:
            if self.db.execute("SELECT 1 FROM memory_hub_meta WHERE key='profiles_v1'").fetchone():
                return
            for r in self.db.execute('SELECT * FROM affections').fetchall():
                row = dict(r)
                revision = self.db.execute("SELECT * FROM affection_revisions WHERE account=? AND subject=? AND source_key!='' ORDER BY id DESC LIMIT 1",
                                           (row['account'], row['subject'])).fetchone()
                self.sync_profile(row, actor='migration', chat=revision['chat'] if revision else '',
                                  source_key=revision['source_key'] if revision else '')
            self.db.execute("INSERT INTO memory_hub_meta VALUES('profiles_v1',?)", (str(time.time()),))

    def sync_mask(self, value, *, actor='model', source_keys=()):
        if actor == 'model' and (not self.enabled() or not self.settings.memory_consolidation_enabled):
            return
        origin_id = self.role() + '/' + value['chat']
        origin = self.db.execute("SELECT * FROM memory_origins WHERE account=? AND source='mask' AND origin_id=?",
                                 (value['account'], origin_id)).fetchone()
        before = dict(self.db.execute('SELECT * FROM memories WHERE id=?', (origin['memory_id'],)).fetchone()) if origin else None
        body = '本群相处风格：' + str(value.get('vibe_summary') or '自然温和')
        if before and before['content'] == body and before['projection_json'] == json.dumps(value,ensure_ascii=False):
            return
        if before and before['status'] != 'active' and actor != 'admin':
            return
        if before:
            identity = before['id']
            self.db.execute("UPDATE memories SET content=?,updated=?,status='active',archived=0,version=version+1 WHERE id=?", (body, value['updated'], identity))
        else:
            cur = self.db.execute('''INSERT INTO memories(account,chat,subject,kind,scope,content,source_key,actor,
                version,created,updated,tier,status,role_id,venue,source_class)
                VALUES(?,?,?,'chat_style','chat',?,'','model',1,?,?,'stable','active',?,'group','mask')''',
                (value['account'], value['chat'], 'self', body, value['updated'], value['updated'], self.role()))
            identity = cur.lastrowid
            self.db.execute("INSERT INTO memory_origins VALUES(?,'mask',?,?,1)", (value['account'], origin_id, identity))
        row = dict(self.db.execute('SELECT * FROM memories WHERE id=?', (identity,)).fetchone())
        self.db.execute('UPDATE memories SET projection_json=?,locked=? WHERE id=?',
                        (json.dumps(value,ensure_ascii=False),int(bool(value.get('locked'))),identity))
        for key in source_keys:
            self.evidence(row,key,'group_style')
        self.book._revision(value['account'], row, before, actor, 'group_style')

    def migrate_masks(self):
        with self.store.lock, self.db:
            if self.db.execute("SELECT 1 FROM memory_hub_meta WHERE key='masks_v1'").fetchone():return
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='persona_group_masks'").fetchone():
                for row in self.db.execute('SELECT * FROM persona_group_masks').fetchall():
                    self.sync_mask(dict(row),actor='migration')
            self.db.execute("INSERT INTO memory_hub_meta VALUES('masks_v1',?)",(str(time.time()),))

    def mask_view(self, account, chat):
        row = self.db.execute('''SELECT m.* FROM memory_origins o JOIN memories m ON m.id=o.memory_id
            WHERE o.account=? AND o.source='mask' AND o.origin_id=?''',(account,self.role()+'/'+chat)).fetchone()
        if not self.enabled() or not row or row['status']!='active' or row['tier']=='cold':return None
        value=json.loads(row['projection_json'])
        value['vibe_summary']=row['content'].removeprefix('本群相处风格：')
        value['locked']=row['locked']
        return value

    def mask_active(self, account, chat):
        r = self.db.execute('''SELECT m.status,m.tier FROM memory_origins o JOIN memories m ON m.id=o.memory_id
            WHERE o.account=? AND o.source='mask' AND o.origin_id=?''', (account, self.role() + '/' + chat)).fetchone()
        return self.enabled() and bool(r and r['status'] == 'active' and r['tier'] != 'cold')

    def visible(self,row,chat,subjects,shared):
        if not row or not self.enabled():return False
        if row.get('role_id') and row['role_id']!=self.role():return False
        venue='group' if chat.endswith('@chatroom') or chat=='moments' else 'private'
        if row.get('venue','any') not in ('any',venue):return False
        return self.book.visible(row,chat,subjects,shared)

    def recall(self, account, allowed, subject, question, *, chat=None, subjects=(), shared=True,
               limit=8, diagnostics=None, include_cold=False, at_time=None, personal_priority=0,
               include_jokes=True, half_life_days=21):
        if not allowed or limit <= 0 or not self.enabled():
            if diagnostics is not None:
                diagnostics.update(available_in_scope=0)
            return []
        from .memory import recall_terms
        people = sorted({p for p in (*subjects, subject) if p})
        marks = ','.join('?' for _ in allowed)
        where = f'account=? AND chat IN ({marks})'
        args = [account, *allowed]
        if chat is not None:
            person_marks = ','.join('?' for _ in people) or "''"
            where += f" AND ((chat=? AND scope='chat') OR (scope='profile' AND kind IN ('fact','preference','nickname','interaction_rule','impression') AND subject IN ({person_marks}) AND (? OR chat=?)))"
            args += [chat, *people, int(shared), chat]
        now = time.time()
        if at_time is None:
            where += " AND status='active' AND archived=0 AND (expires_at=0 OR expires_at>?)"
            args.append(now)
        else:
            where += " AND status IN ('active','superseded') AND valid_from<=? AND (valid_until=0 OR valid_until>?)"
            args += [at_time, at_time]
        where += " AND (role_id='' OR role_id=?)"
        args.append(self.role())
        if chat:
            venue = 'group' if chat.endswith('@chatroom') or chat == 'moments' else 'private'
            where += " AND venue IN ('any',?)"
            args.append(venue)
        if not include_cold:
            where += " AND tier!='cold'"
        if not include_jokes:
            where += " AND kind!='joke'"
        with self.store.lock:
            rows = [dict(r) for r in self.db.execute('SELECT * FROM memories WHERE ' + where, args)]
        if diagnostics is not None:
            diagnostics.update(available_in_scope=len(rows))
        words = recall_terms(question)
        lowered = str(question).casefold()
        def score(row):
            related = sum(w in row['content'].casefold() for w in words)
            tags = [t.strip() for t in re.split(r'[,，、\s/#]+', row['triggers']) if len(t.strip()) >= 2]
            related += 2 * sum(t.casefold() in lowered for t in tags)
            personal = row['subject'] in people and row['scope'] == 'profile'
            style = row['kind'] in ('chat_style', 'interaction_rule', 'nickname')
            # Preserve legacy room-scoped administrator corrections without
            # turning an unresolved old nickname into a shared wxid profile.
            room_correction = row['actor']=='admin' and row['kind'] in FACT_KINDS and row['scope']=='chat' and row['chat']==chat
            if not related and not personal and not style and not room_correction:
                return 0
            if row['kind'] == 'joke' and self.settings and self.settings.callback_frequency == 'off':
                return 0
            if row['kind'] in ('event', 'joke', 'meme_usage', 'slang', 'shared_moment', 'self_stance') and not related:
                return 0
            age = max(0, (now - row['updated']) / 86400)
            strength = {'core': 12, 'stable': 6, 'ordinary': 1, 'cold': 0}[row['tier']]
            decay = max(.2, .5 ** (age / max(3,half_life_days))) if row['tier'] == 'ordinary' else 1
            affinity = 2*min(2,max(0,personal_priority)) if related and personal else 0
            return (min(related, 12) * 8 + 10 * personal + strength + affinity + (2 if row['actor'] == 'admin' else 0)) * decay
        ranked = sorted(((score(r), r) for r in rows), key=lambda p: (p[0], p[1]['updated'], p[1]['id']), reverse=True)
        return [r for score_value, r in ranked if score_value > 0][:min(50, limit)]

    def mark_loaded(self, account, identities):
        with self.store.lock, self.db:
            self.db.executemany('''UPDATE memories SET last_recalled=?,recall_count=recall_count+1
                WHERE account=? AND id=? AND status='active' ''',
                [(time.time(),account,i) for i in set(identities)])

    def mark_used(self, account, identities, loaded):
        # This is model-reported use after confirmed delivery, never new evidence.
        reported={i for i in (identities if isinstance(identities,list) else []) if isinstance(i,int) and not isinstance(i,bool)}
        with self.store.lock, self.db:
            self.db.executemany('''UPDATE memories SET last_used=?,use_count=use_count+1
                WHERE account=? AND id=? AND status='active' ''',
                [(time.time(),account,i) for i in reported.intersection(loaded)])

    def maintenance(self, settings):
        if not settings.memory_enabled or not settings.memory_consolidation_enabled:
            return
        now = time.time()
        with self.store.lock, self.db:
            rows = self.db.execute('''SELECT * FROM memories WHERE account=? AND status='active' AND locked=0
                AND ((expires_at>0 AND expires_at<=?) OR (tier='ordinary' AND max(updated,last_used)<?)) LIMIT 40''',
                (settings.account_id, now, now - settings.memory_cold_after_days * 86400)).fetchall()
            for source in rows:
                old = dict(source)
                # An unfinished commitment must be explicit rather than inferred from age.
                if old['kind'] == 'self_stance':
                    continue
                expired = bool(old['expires_at'] and old['expires_at'] <= now)
                self.db.execute('''UPDATE memories SET tier='cold',status=?,version=version+1 WHERE id=?''',
                                ('expired' if expired else 'active', old['id']))
                value = dict(self.db.execute('SELECT * FROM memories WHERE id=?', (old['id'],)).fetchone())
                self.book._revision(old['account'], value, old, 'system', 'expire' if expired else 'cool')
                self.project_learning(value)

    def detail(self, account, allowed, identity):
        row = self.book.get(account, identity, allowed)
        if not row:
            return None
        with self.store.lock:
            evidence = [dict(r) for r in self.db.execute('SELECT * FROM memory_evidence WHERE memory_id=? ORDER BY id DESC LIMIT 30', (identity,))]
            revisions = [dict(r) for r in self.db.execute('SELECT * FROM memory_revisions WHERE memory_id=? AND account=? ORDER BY id DESC LIMIT 40', (identity, account))]
            origins = [dict(r) for r in self.db.execute('SELECT * FROM memory_origins WHERE memory_id=? AND account=?', (identity, account))]
            legacy = []
            for origin in origins:
                if origin['source'] == 'learning':
                    legacy.extend(dict(r) for r in self.db.execute('SELECT * FROM learning_revisions WHERE proposal_id=? AND account=? ORDER BY id DESC LIMIT 40', (origin['origin_id'], account)))
        return {'memory': row, 'evidence': evidence, 'revisions': revisions, 'origins': origins, 'legacy_revisions': legacy}

    def snapshot(self, settings, *, query='', chat='', subject='', tier='', status='', kind='', view='', limit=60, before=0):
        if chat and chat not in settings.allowed_chat_ids:
            raise ValueError('该会话未授权')
        if tier and tier not in TIERS or status and status not in STATES or kind and kind not in KINDS:
            raise ValueError('记忆筛选无效')
        if not settings.allowed_chat_ids:
            return {'memories': [], 'counts': {}, 'total': 0, 'next': None}
        marks = ','.join('?' for _ in settings.allowed_chat_ids)
        where = f'account=? AND chat IN ({marks})'
        args = [settings.account_id, *settings.allowed_chat_ids]
        if view == 'people':where += " AND kind IN ('fact','preference','nickname','interaction_rule','impression','relationship') AND subject!='self'"
        elif view == 'groups':where += " AND scope='chat' AND chat LIKE '%@chatroom'"
        elif view == 'growth':where += " AND kind IN ('chat_style','meme_usage','tool_learning','expression','persona','bot_alias','slang','interaction_rule','impression')"
        elif view == 'cold':where += " AND (tier='cold' OR status!='active')"
        elif view not in ('','all'):raise ValueError('记忆视图无效')
        for key, value in (('chat', chat), ('subject', subject), ('tier', tier), ('status', status), ('kind', kind)):
            if value:
                where += ' AND ' + key + '=?'
                args.append(value)
        if query:
            where += ' AND (instr(content,?) OR instr(subject,?) OR instr(triggers,?))'
            args += [query[:200]] * 3
        if before:
            where += ' AND id<?'
            args.append(before)
        with self.store.lock:
            counts = [dict(r) for r in self.db.execute('SELECT tier,status,count(*) AS n FROM memories WHERE ' + where + ' GROUP BY tier,status', args)]
            rows = [dict(r) for r in self.db.execute('SELECT * FROM memories WHERE ' + where + ' ORDER BY id DESC LIMIT ?', [*args, min(100, max(1, limit)) + 1])]
        return {'memories': rows[:limit], 'counts': counts, 'total': sum(r['n'] for r in counts),
                'next': rows[limit-1]['id'] if len(rows) > limit else None, 'enabled': settings.memory_enabled}
