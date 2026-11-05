"""Permissions and expression projections over canonical MemoryBook memories.

No duplicate fact store: these tables contain policy, provenance and expression
metadata only. Private WeChat evidence never participates in this source view.
"""
import json
import math
import re
import time
from urllib.parse import urlsplit

from .memory_layers import INTERNET
from .learning import POISON_PATTERN


def source_kind(source):
    host=(urlsplit(str(source.get('url') or '')).hostname or '').casefold()
    if source.get('kind')=='bilibili' or host=='bilibili.com' or host.endswith('.bilibili.com') or host=='b23.tv':return 'bilibili'
    if source.get('kind')=='tieba' or host=='tieba.baidu.com' or host.endswith('.tieba.baidu.com'):return 'tieba'
    return 'legacy_web'


def clean(value,limit=200):return value.strip()[:limit] if isinstance(value,str) else ''


class GrowthMemoryPolicy:
    def __init__(self,growth):
        self.growth=growth;self.runtime=growth.runtime;self.db=growth.db;self.store=growth.store
        with self.store.lock,self.db:
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS growth_memory_policy(memory_id INTEGER PRIMARY KEY,
                    source_kind TEXT NOT NULL,learning_enabled INTEGER NOT NULL DEFAULT 1,
                    chat_enabled INTEGER NOT NULL DEFAULT 0,updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS growth_expressions(memory_id INTEGER PRIMARY KEY,
                    scene TEXT NOT NULL,emotion TEXT NOT NULL,intent TEXT NOT NULL,examples TEXT NOT NULL DEFAULT '[]');
                CREATE TABLE IF NOT EXISTS growth_interest_events(memory_id INTEGER PRIMARY KEY,
                    delta REAL NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS growth_policy_revisions(id INTEGER PRIMARY KEY,
                    memory_id INTEGER NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,
                    created REAL NOT NULL,actor TEXT NOT NULL);
            ''')
        self.migrate()

    def migrate(self):
        moved=0
        with self.store.lock,self.db:
            rows=self.db.execute('''SELECT m.*,i.journal_id FROM memories m JOIN growth_insights i ON i.memory_id=m.id
                WHERE m.source_class IN ('world_learning','bilibili_learning','internet_learning')''').fetchall()
            for raw in rows:
                old=dict(raw)
                sources=self.db.execute('''SELECT s.kind,s.url FROM growth_journals j
                    JOIN json_each(CASE WHEN json_valid(j.sources) THEN j.sources ELSE '[]' END) ids
                    JOIN growth_sources s ON s.id=ids.value WHERE j.id=? AND s.account=?''',
                    (old['journal_id'],old['account'])).fetchall()
                kinds={source_kind(dict(source)) for source in sources}
                kind=next(iter(kinds)) if len(kinds)==1 else 'mixed' if kinds else 'legacy_web'
                self.db.execute('INSERT OR IGNORE INTO growth_memory_policy VALUES(?,?,1,0,?)',(old['id'],kind,time.time()))
                if old['chat']!=INTERNET or old['source_class']!='internet_learning':
                    new={**old,'chat':INTERNET,'source_class':'internet_learning','version':old['version']+1,'updated':time.time()}
                    self.db.execute('UPDATE memories SET chat=?,source_class=?,version=?,updated=? WHERE id=?',
                        (new['chat'],new['source_class'],new['version'],new['updated'],old['id']))
                    self.runtime.memory_book._revision(old['account'],new,old,'system','internet_scope_migration');moved+=1
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bilibili_interest_events'").fetchone():
                self.db.execute('''INSERT OR IGNORE INTO growth_interest_events SELECT memory_id,delta,created
                    FROM bilibili_interest_events WHERE memory_id IN (SELECT memory_id FROM growth_memory_policy)''')
        return moved

    def expression_switch(self,kind):
        s=self.growth.config.settings
        return bool(s.growth_bilibili_expression_shared if kind=='bilibili' else
                    s.growth_tieba_expression_shared if kind=='tieba' else False)

    def chat_allowed(self,row):
        if not row or not self.growth.config.settings.memory_enabled:return False
        row=dict(row)
        if (row.get('account')!=self.growth.config.settings.account_id or row.get('role_id')!=self.growth.hub.role()
                or not self.growth.book.active(row)):return False
        permission=self.db.execute('''SELECT p.*,i.category FROM growth_memory_policy p
            JOIN growth_insights i ON i.memory_id=p.memory_id WHERE p.memory_id=?''',(row['id'],)).fetchone()
        return bool(permission and permission['chat_enabled'] and
                    (permission['category']!='expression' or self.expression_switch(permission['source_kind'])) and self.source_live(row['id']))

    def source_live(self,identity):
        if self.db.execute('''SELECT 1 FROM growth_insights i JOIN bilibili_readings b ON b.journal_id=i.journal_id
                WHERE i.memory_id=? AND b.enabled=0''',(identity,)).fetchone():return False
        rows=self.db.execute('''SELECT s.url FROM growth_insights i JOIN growth_journals j ON j.id=i.journal_id
            JOIN json_each(CASE WHEN json_valid(j.sources) THEN j.sources ELSE '[]' END) ids
            JOIN growth_sources s ON s.id=ids.value WHERE i.memory_id=?''',(identity,)).fetchall()
        for row in rows:
            if source_kind({'url':row['url']})!='tieba':continue
            match=re.search(r'/p/(\d+)',row['url'])
            if match and self.db.execute('SELECT 1 FROM tieba_lifecycle WHERE account=? AND tid=? AND (trashed_at>0 OR deleted_at>0)',
                (self.growth.config.settings.account_id,match[1])).fetchone():return False
        return True

    def rows(self,*,include_inactive=False,learning=False,chat=False,source='',before=0,limit=240):
        s=self.growth.config.settings
        result=[]
        where="m.account=? AND m.role_id=? AND m.chat=? AND m.source_class='internet_learning'"
        args=[s.account_id,self.growth.hub.role(),INTERNET]
        if source:where+=' AND p.source_kind=?';args.append(source)
        if before:where+=' AND m.id<?';args.append(before)
        if learning:where+=' AND p.learning_enabled=1'
        if chat:where+=' AND p.chat_enabled=1'
        for raw in self.db.execute('''SELECT m.*,i.journal_id,i.category,i.topic,i.conditions,i.counterexamples,
                p.source_kind,p.learning_enabled,p.chat_enabled,e.scene,e.emotion,e.intent,e.examples
            FROM growth_insights i JOIN memories m ON m.id=i.memory_id
            JOIN growth_memory_policy p ON p.memory_id=m.id LEFT JOIN growth_expressions e ON e.memory_id=m.id
            WHERE '''+where+' ORDER BY m.id DESC LIMIT ?',(*args,min(240,max(1,limit)))):
            row=dict(raw);row['examples']=json.loads(row['examples'] or '[]')
            if not include_inactive and (not self.growth.book.active(row) or row['tier']=='cold' or not self.source_live(row['id'])):continue
            if learning and not row['learning_enabled']:continue
            if chat and not self.chat_allowed(row):continue
            result.append(row)
        return result

    def register(self,identity,kind,category,item):
        scene=clean(item.get('scene'),120);emotion=clean(item.get('emotion'),60);intent=clean(item.get('intent'),100)
        examples=[clean(value,60) for value in (item.get('examples') or [])[:3] if isinstance(value,str)] if isinstance(item.get('examples'),list) else []
        expression=category=='expression' and bool(scene and emotion and intent and examples)
        projected=' '.join([clean(item.get('text'),140),scene,emotion,intent,*examples,
            clean(item.get('conditions'),160),clean(item.get('counterexamples'),160)])
        # No credentials, contact/identity strings or links cross via examples.
        unsafe=bool(POISON_PATTERN.search(projected) or re.search(
            r'https?://|wxid_|@chatroom|\bsk-|\b[A-Za-z_]{12,}\b|\d{5,}|\S+@\S+\.\S+',projected,re.I))
        # Eligibility is separate from the reversible source-wide sharing switch.
        # Turning sharing back on must not require approving each new expression.
        shared=expression and not unsafe and kind in ('bilibili','tieba')
        with self.store.lock,self.db:
            self.db.execute('INSERT OR REPLACE INTO growth_memory_policy VALUES(?,?,1,?,?)',(identity,kind,int(shared),time.time()))
            if expression:self.db.execute('INSERT OR REPLACE INTO growth_expressions VALUES(?,?,?,?,?)',
                (identity,scene,emotion,intent,json.dumps(examples,ensure_ascii=False)))
            delta=item.get('interest_delta',1)
            if category=='interest' and isinstance(delta,(int,float)) and not isinstance(delta,bool) and math.isfinite(delta) and -2<=delta<=2:
                self.db.execute('INSERT OR REPLACE INTO growth_interest_events VALUES(?,?,?)',(identity,delta,time.time()))

    def projection(self,row):
        if not self.chat_allowed(row):return None
        current=self.db.execute('''SELECT m.id,m.version,m.content,i.category,i.conditions,i.counterexamples,
                e.scene,e.emotion,e.intent,e.examples FROM memories m JOIN growth_insights i ON i.memory_id=m.id
                LEFT JOIN growth_expressions e ON e.memory_id=m.id WHERE m.id=?''',(row['id'],)).fetchone()
        if not current:return None
        current=dict(current);current['examples']=json.loads(current['examples'] or '[]')
        # Deliberately omit source bodies, titles, links, author IDs and reflections.
        return {key:current.get(key) for key in ('id','version','content','category','conditions','counterexamples','scene','emotion','intent','examples')}

    def update(self,identity,values):
        before=self.db.execute('SELECT * FROM growth_memory_policy WHERE memory_id=?',(identity,)).fetchone()
        if not before:return
        new=dict(before)
        for key in ('learning_enabled','chat_enabled'):
            if key in values:
                if not isinstance(values[key],bool):raise ValueError('成长使用权限必须为开关值')
                new[key]=int(values[key])
        if any(new[key]!=before[key] for key in ('learning_enabled','chat_enabled')):
            with self.store.lock,self.db:
                self.db.execute('UPDATE growth_memory_policy SET learning_enabled=?,chat_enabled=?,updated=? WHERE memory_id=?',
                    (new['learning_enabled'],new['chat_enabled'],time.time(),identity))
                self.db.execute('INSERT INTO growth_policy_revisions(memory_id,before_json,after_json,created,actor) VALUES(?,?,?,?,?)',
                    (identity,json.dumps(dict(before)),json.dumps(new),time.time(),'admin'))

    def interests(self):
        available={row['id']:row for row in self.rows(learning=True)};topics={}
        for raw in self.db.execute('''SELECT e.* FROM growth_interest_events e JOIN memories m ON m.id=e.memory_id
            WHERE m.account=? AND m.role_id=? ORDER BY e.created DESC LIMIT 600''',
            (self.growth.config.settings.account_id,self.growth.hub.role())):
            row=available.get(raw['memory_id'])
            if not row:continue
            item=topics.setdefault(row['topic'],{'topic':row['topic'],'strength':0,'encounters':0,'thought':row['content'],
                'memory_id':row['id'],'last_change':raw['created'],'sources':[]})
            item['strength']+=raw['delta']*.5**(max(0,time.time()-raw['created'])/(21*86400));item['encounters']+=1
            if row['source_kind'] not in item['sources']:item['sources'].append(row['source_kind'])
        for item in topics.values():item['strength']=round(max(-5,min(5,item['strength'])),2)
        return sorted(topics.values(),key=lambda value:(-value['strength'],-value['last_change']))[:12]
