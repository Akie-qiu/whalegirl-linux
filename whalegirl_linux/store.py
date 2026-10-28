from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from .topic_relevance import topic_terms


class Store:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS inbox(
                key TEXT PRIMARY KEY, account TEXT NOT NULL, chat TEXT NOT NULL,
                sender TEXT NOT NULL, content TEXT NOT NULL, timestamp REAL NOT NULL,
                state TEXT NOT NULL, reply TEXT NOT NULL DEFAULT '',
                receipt TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                updated REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS inbox_state ON inbox(state,timestamp);
            CREATE TABLE IF NOT EXISTS baselines(account TEXT,chat TEXT,PRIMARY KEY(account,chat));
            CREATE TABLE IF NOT EXISTS poll_progress(
                account TEXT,chat TEXT,after_time REAL NOT NULL,after_id INTEGER NOT NULL,
                updated REAL NOT NULL,PRIMARY KEY(account,chat));
            CREATE TABLE IF NOT EXISTS history(
                id INTEGER PRIMARY KEY, account TEXT, chat TEXT, role TEXT, content TEXT
            );
            CREATE TABLE IF NOT EXISTS message_log(
                key TEXT PRIMARY KEY, account TEXT, chat TEXT, sender_id TEXT,
                sender TEXT, kind INTEGER, is_self INTEGER, local_id INTEGER,
                content TEXT, timestamp REAL, description TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS message_log_chat ON message_log(account,chat,timestamp);
            CREATE INDEX IF NOT EXISTS message_log_sender ON message_log(account,sender_id,timestamp);
            CREATE TABLE IF NOT EXISTS archives(account TEXT,chat TEXT,PRIMARY KEY(account,chat));
            CREATE TABLE IF NOT EXISTS reply_metrics(
                key TEXT PRIMARY KEY, metrics TEXT NOT NULL, updated REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS image_cache(
                key TEXT PRIMARY KEY, data TEXT, format TEXT, quality TEXT, partial INTEGER
            );
            CREATE TABLE IF NOT EXISTS outbound_actions(
                id TEXT PRIMARY KEY,inbox_key TEXT NOT NULL,account TEXT NOT NULL,chat TEXT NOT NULL,
                kind TEXT NOT NULL,reply TEXT NOT NULL,state TEXT NOT NULL,sent_at REAL NOT NULL,
                receipt TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '');
            CREATE INDEX IF NOT EXISTS outbound_actions_account ON outbound_actions(account,sent_at);
            CREATE TABLE IF NOT EXISTS archive_progress(
                account TEXT,chat TEXT,before_time REAL,before_id INTEGER,completed REAL NOT NULL DEFAULT 0,
                last_read REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',PRIMARY KEY(account,chat));
            CREATE TABLE IF NOT EXISTS chat_digests(
                account TEXT,chat TEXT,summary TEXT NOT NULL,through_time REAL NOT NULL,
                through_id INTEGER NOT NULL,updated REAL NOT NULL,PRIMARY KEY(account,chat));
            CREATE TABLE IF NOT EXISTS media_attempts(
                key TEXT PRIMARY KEY,status TEXT NOT NULL,reason TEXT NOT NULL,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sticker_usage(
                account TEXT,chat TEXT,md5 TEXT,sent REAL);
        """)
        if 'after_id' not in {r[1] for r in self.db.execute('PRAGMA table_info(archive_progress)')}:
            self.db.execute('ALTER TABLE archive_progress ADD COLUMN after_id INTEGER NOT NULL DEFAULT 0')
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(inbox)')}
        for table in ('inbox','message_log'):
            if 'payload_json' not in {r[1] for r in self.db.execute('PRAGMA table_info('+table+')')}:
                self.db.execute('ALTER TABLE '+table+" ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'")
        if 'local_id' not in columns:
            self.db.execute('ALTER TABLE inbox ADD COLUMN local_id INTEGER')
            self.db.execute("ALTER TABLE inbox ADD COLUMN sender_id TEXT NOT NULL DEFAULT ''")
            self.db.execute('UPDATE inbox SET local_id=(SELECT local_id FROM message_log WHERE message_log.key=inbox.key),sender_id=COALESCE((SELECT sender_id FROM message_log WHERE message_log.key=inbox.key),\'\')')
            for row in self.db.execute("SELECT key FROM inbox WHERE local_id IS NULL AND key LIKE '%/local:%'").fetchall():
                value=row[0].rsplit('/local:',1)[-1]
                if value.isdigit():self.db.execute('UPDATE inbox SET local_id=? WHERE key=?',(int(value),row[0]))
        self.db.execute('CREATE INDEX IF NOT EXISTS inbox_native_id ON inbox(account,chat,local_id)')
        if 'details_json' not in {r[1] for r in self.db.execute('PRAGMA table_info(outbound_actions)')}:
            self.db.execute("ALTER TABLE outbound_actions ADD COLUMN details_json TEXT NOT NULL DEFAULT '{}'")
        self.db.execute('CREATE INDEX IF NOT EXISTS outbound_pending ON outbound_actions(account,chat,state,sent_at)')
        if 'trigger' not in {r[1] for r in self.db.execute('PRAGMA table_info(inbox)')}:
            self.db.execute("ALTER TABLE inbox ADD COLUMN trigger TEXT NOT NULL DEFAULT ''")
        # An interrupted UI send can already have reached WeChat. Never resend it.
        self.db.execute("UPDATE inbox SET state='uncertain',error='进程在发送阶段中断，请人工核对' WHERE state='sending'")
        self.db.execute("UPDATE outbound_actions SET state='uncertain',error='发送阶段中断，请人工核对' WHERE state='sending'")
        self.db.execute("UPDATE inbox SET state='queued',error='' WHERE state='generating'")
        self.db.execute("UPDATE inbox SET state='queued',error='等待期间服务重启，已按普通流程恢复' WHERE state='waiting'")
        self.db.commit()

    def observe(self, events, *, commit=True):
        events=[{'payload_json':'{}',**event} for event in events]
        with self.lock:
            self.db.executemany("""INSERT INTO message_log
                (key,account,chat,sender_id,sender,kind,is_self,local_id,content,timestamp,payload_json)
                VALUES(:key,:account,:chat,:sender_id,:sender,:kind,:is_self,:local_id,:content,:timestamp,:payload_json)
                ON CONFLICT(key) DO UPDATE SET sender_id=excluded.sender_id,
                sender=excluded.sender,content=excluded.content,kind=excluded.kind,
                is_self=excluded.is_self,local_id=excluded.local_id,payload_json=excluded.payload_json
                WHERE message_log.content!=excluded.content OR message_log.sender_id!=excluded.sender_id
                OR message_log.sender!=excluded.sender OR message_log.is_self!=excluded.is_self
                OR message_log.payload_json!=excluded.payload_json""", events)
            if commit:
                self.db.commit()

    def ingest_poll(self, account, chat, entries, cursor, *, baseline=False):
        """Commit inbox, context and live cursor together, once per chat batch."""
        inserted = []
        with self.lock, self.db:
            for event, state in entries:
                if self.insert(event, state, commit=False):
                    inserted.append(event)
            self.observe([event for event, _ in entries], commit=False)
            self.save_poll_cursor(account, chat, cursor, commit=False)
            if baseline:
                self.db.execute("INSERT OR IGNORE INTO baselines VALUES(?,?)", (account, chat))
        return inserted

    def archived(self, account, chat):
        return bool(self.db.execute("SELECT 1 FROM archives WHERE account=? AND chat=?", (account, chat)).fetchone())

    def archive_done(self, account, chat):
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO archives VALUES(?,?)", (account, chat))
            self.db.commit()

    def conversation(self, account, chat, limit=60):
        # A pending native row is seen again under its server ID after upload.
        # Count that message once, without merging real repeated messages.
        grouped = {}
        rows = self.db.execute("SELECT * FROM message_log WHERE account=? AND chat=? "
                               "ORDER BY timestamp DESC,local_id DESC,key DESC", (account, chat))
        for raw in rows:
            row = dict(raw)
            identity = ('local', row['local_id']) if (row.get('local_id') or 0) > 0 else ('key', row['key'])
            if identity not in grouped:
                if len(grouped) >= limit:
                    break
                grouped[identity] = {**row, 'alias_keys': [row['key']]}
                continue
            previous = grouped[identity]
            aliases = previous['alias_keys'] + [row['key']]
            if '/local:' in previous['key'] and '/local:' not in row['key']:
                row['description'] = row.get('description') or previous.get('description') or ''
                grouped[identity] = {**row, 'alias_keys': aliases}
            else:
                previous['alias_keys'] = aliases
                previous['description'] = previous.get('description') or row.get('description') or ''
        return sorted(grouped.values(), key=lambda r: (r['timestamp'], r.get('local_id') or 0, r['key']))

    def find_message(self, account, chat, server_id):
        row=self.db.execute('SELECT * FROM message_log WHERE key=? AND account=? AND chat=?',
                            (f'{account}/{chat}/{server_id}',account,chat)).fetchone()
        return dict(row) if row else None

    def latest_cursor(self,account,chat):
        row=self.db.execute('SELECT timestamp,local_id FROM message_log WHERE account=? AND chat=? ORDER BY timestamp DESC,local_id DESC LIMIT 1',
                            (account,chat)).fetchone()
        return (float(row['timestamp']),int(row['local_id'] or 0)) if row else None

    def poll_cursor(self, account, chat):
        # Archive imports must never move the live reader past unprocessed rows.
        row = self.db.execute('SELECT after_time,after_id FROM poll_progress WHERE account=? AND chat=?',
                              (account, chat)).fetchone()
        if row:
            return float(row[0]), int(row[1])
        row = self.db.execute('SELECT timestamp,local_id FROM inbox WHERE account=? AND chat=? '
                              'ORDER BY timestamp DESC,local_id DESC LIMIT 1', (account, chat)).fetchone()
        return (float(row[0]), int(row[1] or 0)) if row else None

    def save_poll_cursor(self, account, chat, cursor, *, commit=True):
        if cursor is None:
            return
        with self.lock:
            self.db.execute('''INSERT INTO poll_progress VALUES(?,?,?,?,?)
                ON CONFLICT(account,chat) DO UPDATE SET after_time=excluded.after_time,
                after_id=excluded.after_id,updated=excluded.updated
                WHERE excluded.after_time>poll_progress.after_time OR
                (excluded.after_time=poll_progress.after_time AND excluded.after_id>poll_progress.after_id)''',
                (account, chat, cursor[0], cursor[1], time.time()))
            if commit:
                self.db.commit()

    def received(self, account, allowed, chat='', query='', before_time=None, before_id=0, before_key='', limit=50):
        scope=[chat] if chat and chat in allowed else allowed if not chat else []
        if not scope:return {'messages':[],'next':None}
        marks=','.join('?' for _ in scope)
        where=f'm.account=? AND m.chat IN ({marks})';args=[account,*scope]
        if query:where+=' AND (instr(m.content,?)>0 OR instr(m.sender,?)>0)';args += [query[:200],query[:200]]
        if before_time is not None:
            where+=' AND (m.timestamp<? OR (m.timestamp=? AND (m.local_id<? OR (m.local_id=? AND m.key<?))))'
            args += [before_time,before_time,before_id,before_id,before_key]
        rows=self.db.execute(f'''WITH ranked AS (
            SELECT m.*,ROW_NUMBER() OVER (PARTITION BY m.chat,
                CASE WHEN m.local_id>0 THEN CAST(m.local_id AS TEXT) ELSE m.key END
                ORDER BY CASE WHEN m.key LIKE '%/local:%' THEN 0 ELSE 1 END DESC,m.key DESC) AS rank
            FROM message_log m WHERE {where})
            SELECT m.*,i.state,i.error,i.reply,a.status AS media_status,a.reason AS media_reason
            FROM ranked m LEFT JOIN inbox i ON i.key=(
                SELECT x.key FROM inbox x WHERE x.account=m.account AND x.chat=m.chat
                AND (x.key=m.key OR (m.local_id>0 AND x.local_id=m.local_id))
                ORDER BY CASE WHEN x.state IN ('sent','partial','sending','uncertain','failed') THEN 1 ELSE 0 END DESC,
                x.updated DESC LIMIT 1)
            LEFT JOIN media_attempts a ON a.key=m.key
            WHERE m.rank=1 ORDER BY m.timestamp DESC,m.local_id DESC,m.key DESC LIMIT ?''',
            (*args,min(100,max(1,limit))+1)).fetchall()
        values=[]
        for r in rows[:limit]:
            v=dict(r);v['payload']=json.loads(v.pop('payload_json','{}'))
            v['state']=v['state'] or 'archived';values.append(v)
        last=values[-1] if len(rows)>limit and values else None
        return {'messages':values,'next':{'before_time':last['timestamp'],'before_id':last['local_id'],'before_key':last['key']} if last else None}

    def media_result(self,key,status,reason=''):
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO media_attempts VALUES(?,?,?,?)',(key,status,reason[:250],time.time()))
            self.db.commit()

    def digest(self,account,chat):
        r=self.db.execute('SELECT * FROM chat_digests WHERE account=? AND chat=?',(account,chat)).fetchone()
        return dict(r) if r else None

    def save_digest(self,account,chat,summary,row):
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO chat_digests VALUES(?,?,?,?,?,?)',
                (account,chat,summary[:2200],row['timestamp'],row['local_id'],time.time()))
            self.db.commit()

    def used_stickers(self,account,chat,seconds):
        return {r[0] for r in self.db.execute('SELECT md5 FROM sticker_usage WHERE account=? AND chat=? AND sent>?',
                                            (account,chat,time.time()-seconds))} if seconds else set()

    def record_sticker(self,account,chat,md5):
        with self.lock:
            self.db.execute('INSERT INTO sticker_usage VALUES(?,?,?,?)',(account,chat,md5,time.time()));self.db.commit()

    def memory(self, account, allowed_chats, chat, sender_id, question, exclude=(), limit=14):
        if not allowed_chats:
            return []
        marks = ','.join('?' for _ in allowed_chats)
        rows = self.db.execute(f"SELECT * FROM message_log WHERE account=? AND chat IN ({marks}) AND is_self=0 ORDER BY timestamp DESC LIMIT 12000",
                               (account, *allowed_chats)).fetchall()
        words = topic_terms(question)
        if not words:
            return []
        omitted = set(exclude)
        if getattr(self,'memory_hub',None):
            omitted.update(self.memory_hub.suppressed_sources(account,allowed_chats))
        scored = []
        for i, row in enumerate(rows):
            r = dict(row)
            if r['key'] in omitted:
                continue
            content = (r['content'] + ' ' + r['description']).casefold()
            hits = sum(word in content for word in words)
            same_person = bool(sender_id and sender_id == r['sender_id'])
            # Source messages are durable memory; no model-invented facts are stored.
            fact = bool(re.search(r'记住|我叫|生日|我喜欢|我不喜欢|我是|我的名字|我的职业|我住', content))
            # Identity can rank a relevant source, but must never make an
            # unrelated old topic relevant by itself.
            score = hits * 5 + same_person * 2 + (same_person and fact) * 5
            if hits > 0 and score > 0:
                scored.append((score, -i, r))
        best = [r for _, _, r in sorted(scored, key=lambda x:(x[0],x[1]), reverse=True)[:limit]]
        return sorted(best, key=lambda r:r['timestamp'])

    def describe(self, key, text):
        with self.lock:
            self.db.execute("UPDATE message_log SET description=? WHERE key=?", (text[:1400], key))
            self.db.commit()

    def metrics(self, key, value):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO reply_metrics VALUES(?,?,?)", (key,json.dumps(value),time.time()))
            self.db.commit()

    def memory_status(self):
        return {'messages':self.db.execute('SELECT count(*) FROM message_log').fetchone()[0],
                'image_descriptions':self.db.execute("SELECT count(*) FROM message_log WHERE description!=''").fetchone()[0],
                'archived_chats':self.db.execute('SELECT count(*) FROM archives').fetchone()[0]}

    def insert(self, event: dict, state="queued", *, commit=True):
        with self.lock:
            existing = self.db.execute('SELECT * FROM inbox WHERE key=? OR '
                '(account=? AND chat=? AND local_id=? AND local_id>0) LIMIT 1',
                (event['key'], event['account'], event['chat'], event.get('local_id') or 0)).fetchone()
            if existing:
                payload = event.get('payload_json', '{}')
                enriched = (not existing['sender_id'] and event.get('sender_id') or
                    existing['content'] != event['content'] or existing['payload_json'] != payload or
                    existing['trigger'] != event.get('trigger', ''))
                retry = (enriched and state == 'queued' and existing['state'] == 'ignored' and
                    existing['error'] in ('', '已收录，未符合当前会话唤醒规则或消息类型'))
                if enriched or existing['sender'] != event['sender']:
                    self.db.execute('''UPDATE inbox SET content=?,sender=?,sender_id=?,payload_json=?,trigger=?,
                        state=?,error=?,updated=? WHERE key=?''',
                        (event['content'],event['sender'],event.get('sender_id') or existing['sender_id'],payload,
                         event.get('trigger',''), 'queued' if retry else existing['state'],
                         '' if retry else existing['error'],time.time() if retry else existing['updated'],existing['key']))
                    if commit:
                        self.db.commit()
                return bool(retry)
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO inbox(key,account,chat,sender,content,timestamp,state,updated,local_id,sender_id,trigger,payload_json,error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (event["key"], event["account"], event["chat"], event["sender"], event["content"], event["timestamp"], state, time.time(),event.get('local_id'),event.get('sender_id',''),event.get('trigger',''),event.get('payload_json','{}'),event.get('read_reason','')[:300]),
            )
            if commit:
                self.db.commit()
            return cursor.rowcount == 1

    def cached_image(self,key):
        row=self.db.execute('SELECT data,format,quality,partial FROM image_cache WHERE key=?',(key,)).fetchone()
        return dict(row) if row else {}

    def cache_image(self,key,media):
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO image_cache VALUES(?,?,?,?,?)',
                            (key,media['data'],media.get('format','jpeg'),media.get('quality',''),int(bool(media.get('partial')))))
            self.db.commit()

    def update(self, key, state, *, reply="", receipt="", error=""):
        with self.lock:
            self.db.execute("UPDATE inbox SET state=?,reply=?,receipt=?,error=?,updated=? WHERE key=?",
                            (state, reply, receipt, error[:300], time.time(), key))
            self.db.commit()

    def baseline_exists(self, account, chat):
        return bool(self.db.execute("SELECT 1 FROM baselines WHERE account=? AND chat=?", (account, chat)).fetchone())

    def baseline(self, account, chat):
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO baselines VALUES(?,?)", (account, chat))
            self.db.commit()

    def queue(self, exclude_chats=()):
        exclude_chats=tuple(exclude_chats or ())
        sql="SELECT * FROM inbox WHERE state='queued'"
        args=[]
        if exclude_chats:
            sql+=' AND chat NOT IN ('+','.join('?' for _ in exclude_chats)+')'
            args.extend(exclude_chats)
        row = self.db.execute(sql+" ORDER BY timestamp,key LIMIT 1",args).fetchone()
        return dict(row) if row else None

    def queued_chat(self, account, chat):
        return [dict(r) for r in self.db.execute("SELECT * FROM inbox WHERE state='queued' AND account=? AND chat=? ORDER BY timestamp,key LIMIT 15", (account, chat))]

    def messages_after(self, account, chat, after_time, after_id, *, received_after=0, limit=15):
        """Return newly received incoming messages after a wait anchor."""
        rows=self.db.execute("""SELECT m.*,COALESCE(i.trigger,'natural') AS trigger,
                COALESCE(i.state,'ignored') AS state,COALESCE(i.error,'') AS inbox_error,
                COALESCE(i.updated,m.timestamp) AS received_at
            FROM message_log m LEFT JOIN inbox i ON i.key=m.key
            WHERE m.account=? AND m.chat=? AND m.is_self=0
              AND (m.timestamp>? OR (m.timestamp=? AND COALESCE(m.local_id,0)>?))
              AND COALESCE(i.updated,m.timestamp)>=?
            ORDER BY m.timestamp,m.local_id,m.key LIMIT ?""",
            (account,chat,after_time,after_time,after_id,received_after,max(1,min(30,int(limit))))).fetchall()
        return [dict(row) for row in rows]

    def action_start(self, key, event, index, action):
        action_id=f'{key}#part-{index}'
        with self.lock:
            self.db.execute('''INSERT INTO outbound_actions(id,inbox_key,account,chat,kind,reply,state,sent_at)
                VALUES(?,?,?,?,?,?,'sending',?)''',(action_id,key,event['account'],event['chat'],action['type'],action['display'],time.time()))
            self.db.commit()
        return action_id

    def action_finish(self, action_id, state, receipt='', error='', *, details=None):
        with self.lock:
            self.db.execute('UPDATE outbound_actions SET state=?,receipt=?,error=? WHERE id=?',
                            (state,receipt,error[:300],action_id))
            if details is not None:
                self.db.execute('UPDATE outbound_actions SET details_json=? WHERE id=?',
                                (json.dumps(details,ensure_ascii=False),action_id))
            self.db.commit()

    def actions(self, key):
        return [dict(r) for r in self.db.execute('SELECT * FROM outbound_actions WHERE inbox_key=? ORDER BY sent_at,id',(key,))]

    def get(self, key):
        row = self.db.execute("SELECT * FROM inbox WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None

    def latest_sent(self, account, chat):
        row = self.db.execute("""SELECT MAX(COALESCE(json_extract(m.metrics,'$.send_started_at'),i.updated))
            FROM inbox i LEFT JOIN reply_metrics m ON i.key=m.key WHERE i.account=? AND i.chat=? AND i.state IN ('sent','partial')""", (account, chat)).fetchone()
        sent=self.db.execute("SELECT MAX(sent_at) FROM outbound_actions WHERE account=? AND chat=? AND state IN ('sent','uncertain')",
                             (account,chat)).fetchone()[0] or 0
        return max(row[0] or 0,sent)

    def context(self, account, chat, pairs):
        if not pairs:
            return []
        rows = self.db.execute("SELECT role,content FROM history WHERE account=? AND chat=? ORDER BY id DESC LIMIT ?",
                               (account, chat, pairs * 2)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def remember(self, event, reply):
        with self.lock:
            turns = [("assistant", reply)] if event.get('trigger') in ('proactive', 'tieba_context') else [("user", event["content"]), ("assistant", reply)]
            for role, content in turns:
                self.db.execute("INSERT INTO history(account,chat,role,content) VALUES(?,?,?,?)",
                                (event["account"], event["chat"], role, content))
            self.db.commit()

    def recent(self, include_ignored=False):
        where = '' if include_ignored else "WHERE state NOT IN ('baseline','ignored')"
        order = 'timestamp DESC,updated DESC' if include_ignored else 'updated DESC'
        rows=[dict(r) for r in self.db.execute('SELECT key,chat,sender,content,state,reply,error,updated,timestamp '
                                             'FROM inbox '+where+' ORDER BY '+order+' LIMIT 50')]
        for row in rows:
            row['actions']=self.actions(row['key'])
            for action in row['actions']:
                details=json.loads(action.pop('details_json','{}') or '{}')
                action['delivery']={k:details[k] for k in ('attempts','initial_result','final_result',
                    'ui_seconds','ui_lock_seconds','ui_timing','confirmed_at','confirmation') if k in details}
        return rows

    def stats(self):
        return {row[0]: row[1] for row in self.db.execute("SELECT state,COUNT(*) FROM inbox GROUP BY state")}

    def close(self):
        self.db.close()
