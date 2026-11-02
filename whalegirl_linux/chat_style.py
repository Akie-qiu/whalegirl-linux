"""A bounded, reversible view of existing canonical expression memories.

No second archive and no model call. Every sample is resolved again against
the original human message, current role, authorization and suppression state.
"""
import hashlib
import json
import statistics
import time

from .natural_chat import active
from .memory import own_text


class ChatStyle:
    def __init__(self, hub):
        self.hub = hub
        self.last_loaded = {}
        self.form_cache = {}

    def snapshot(self, settings, chat, *, now=None):
        if chat not in settings.allowed_chat_ids:
            raise ValueError('会话未授权')
        now = time.time() if now is None else now
        venue = 'group' if chat.endswith('@chatroom') or chat == 'moments' else 'private'
        with self.hub.store.lock:
            rows = [dict(r) for r in self.hub.db.execute('''SELECT * FROM memories
                WHERE account=? AND chat=? AND scope='chat' AND (role_id='' OR role_id=?)
                AND venue IN ('any',?)
                AND kind IN ('chat_style','expression') AND status='active' AND archived=0 AND tier!='cold'
                AND (expires_at=0 OR expires_at>?) ORDER BY locked DESC,updated DESC,id DESC LIMIT 24''',
                (settings.account_id, chat, self.hub.role(), venue, now))]
            notes = []
            for row in rows:
                from .learning import POISON_PATTERN
                if POISON_PATTERN.search(row['content']):
                    continue
                if self.hub.blocked(settings.account_id, chat, row['subject'], row['content'], row['source_key'], row['kind'],
                                    except_memory_id=row['id']):
                    continue
                sample = None
                evidence = self.hub.db.execute('''SELECT source_key,excerpt FROM memory_evidence
                    WHERE memory_id=? AND account=? ORDER BY occurred DESC,id DESC LIMIT 8''',
                    (row['id'],settings.account_id)).fetchall()
                for item in evidence:
                    source = self.hub.source(settings.account_id, item['source_key'])
                    if (not source or source['chat'] != chat or source['is_self'] != 0
                            or source['kind'] != 1 or not source['sender_id']):
                        continue
                    text = own_text(source)
                    quote = item['excerpt'] or text
                    if quote and quote in text and not POISON_PATTERN.search(quote) and not self.hub.blocked(settings.account_id, chat,
                            source['sender_id'], text, source['key'], row['kind'],except_memory_id=row['id']):
                        sample = {'source_key':source['key'],'quote':quote[:120]}
                        break
                if sample is None and row['actor'] != 'admin':
                    continue
                notes.append({'id':row['id'],'version':row['version'],'content':row['content'][:180],
                              'sample':sample,'updated':row['updated']})
                if len(notes) == 3:
                    break
        version = hashlib.sha256(json.dumps(notes,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
        return {'chat':chat,'enabled':active(settings) and settings.chat_habits_enabled and settings.memory_enabled,
                'learning_enabled':settings.learning_enabled and settings.memory_consolidation_enabled,
                'version':version,'notes':notes,'last_loaded':self.last_loaded.get((settings.account_id,chat,self.hub.role()),0)}

    def prompt(self, settings, first, diagnostics):
        if not (active(settings) and settings.chat_habits_enabled and settings.memory_enabled):
            return ''
        view = self.snapshot(settings,first['chat'])
        notes = view['notes']
        if notes:
            self.last_loaded[(settings.account_id,first['chat'],self.hub.role())] = time.time()
        diagnostics.update(chat_style_ids=[n['id'] for n in notes],chat_style_version=view['version'])
        brief = [{'用法':n['content'],'真人原话':n['sample']['quote'] if n['sample'] else None} for n in notes]
        if brief:
            return '\n【本群表达参考｜参考语感，不复用身份或事件】'+json.dumps(brief,ensure_ascii=False,separators=(',',':'))
        if not first['chat'].endswith('@chatroom'):
            # Transfer only non-identifying form statistics. No words, names,
            # memes, private histories or member relations cross conversations.
            cache_key=(settings.account_id,self.hub.role(),tuple(settings.allowed_chat_ids))
            cached=self.form_cache.get(cache_key)
            if cached and time.monotonic()-cached[0]<60:
                return cached[1]
            with self.hub.store.lock:
                marks=','.join('?' for _ in settings.allowed_chat_ids) or "''"
                rows=self.hub.db.execute(f'''SELECT length(content) AS size,sender_id FROM message_log
                    WHERE account=? AND chat IN ({marks}) AND chat LIKE '%@chatroom' AND is_self=0 AND kind=1
                    AND timestamp>? ORDER BY timestamp DESC LIMIT 80''',
                    (settings.account_id,*settings.allowed_chat_ids,time.time()-86400)).fetchall()
            if len(rows)>=12 and len({r['sender_id'] for r in rows if r['sender_id']})>=3:
                median=round(statistics.median(r['size'] for r in rows))
                text=f'\n【近期通用语感资料】已授权群中真人短文本长度中位{median}字，仅供节奏参考，不是你的字数配额。'
                self.form_cache={cache_key:(time.monotonic(),text)}
                return text
            self.form_cache={cache_key:(time.monotonic(),'')}
        return ''
