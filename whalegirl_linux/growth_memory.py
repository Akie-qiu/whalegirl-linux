"""Permissions and expression projections over canonical MemoryBook memories.

No duplicate fact store: these tables contain policy, provenance and expression
metadata only. Private WeChat evidence never participates in this source view.
"""
import json
import hashlib
import math
import re
import time
import unicodedata
from urllib.parse import urlsplit

from .memory_layers import INTERNET
from .learning import POISON_PATTERN


def source_kind(source):
    host=(urlsplit(str(source.get('url') or '')).hostname or '').casefold()
    if source.get('kind')=='bilibili' or host=='bilibili.com' or host.endswith('.bilibili.com') or host=='b23.tv':return 'bilibili'
    if source.get('kind')=='tieba' or host=='tieba.baidu.com' or host.endswith('.tieba.baidu.com'):return 'tieba'
    return 'legacy_web'


def content_key(source):
    """Independent platform content, never a fetch fingerprint or video segment."""
    try:parsed=urlsplit(str(source.get('url') or ''))
    except ValueError:return ''
    host=(parsed.hostname or '').casefold()
    if host=='bilibili.com' or host.endswith('.bilibili.com'):
        match=re.fullmatch(r'/video/(BV[A-Za-z0-9]{10})/?',parsed.path)
        # Different pages, reading windows and tracking queries are still one
        # video. An unresolved short/AV link is not independently verified here.
        return 'bilibili:'+match[1] if match else ''
    if host=='tieba.baidu.com':
        match=re.fullmatch(r'/p/(\d+)/?',parsed.path)
        return 'tieba:'+str(int(match[1])) if match and int(match[1])>0 else ''
    return ''


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
                CREATE TABLE IF NOT EXISTS growth_personality_evidence(
                    id INTEGER PRIMARY KEY,account TEXT NOT NULL,role_id TEXT NOT NULL,
                    trait_key TEXT NOT NULL,category TEXT NOT NULL,topic TEXT NOT NULL,
                    memory_id INTEGER NOT NULL,journal_id TEXT NOT NULL,source_id TEXT NOT NULL,
                    source_kind TEXT NOT NULL,stance TEXT NOT NULL,excerpt_hash TEXT NOT NULL,excerpt TEXT NOT NULL DEFAULT '',
                    created REAL NOT NULL,delta REAL NOT NULL DEFAULT 1,
                    UNIQUE(account,role_id,trait_key,journal_id,stance));
                CREATE INDEX IF NOT EXISTS growth_personality_evidence_trait
                    ON growth_personality_evidence(account,role_id,trait_key,created);
                CREATE TABLE IF NOT EXISTS growth_personality_states(
                    account TEXT NOT NULL,role_id TEXT NOT NULL,trait_key TEXT NOT NULL,
                    category TEXT NOT NULL,topic TEXT NOT NULL,status TEXT NOT NULL,
                    support_count INTEGER NOT NULL,challenge_count INTEGER NOT NULL,
                    source_count INTEGER NOT NULL,first_seen REAL NOT NULL,last_seen REAL NOT NULL,
                    updated REAL NOT NULL,PRIMARY KEY(account,role_id,trait_key));
                CREATE TABLE IF NOT EXISTS growth_personality_revisions(
                    id INTEGER PRIMARY KEY,account TEXT NOT NULL,role_id TEXT NOT NULL,
                    trait_key TEXT NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,
                    reason TEXT NOT NULL,created REAL NOT NULL);
            ''')
            columns={row[1] for row in self.db.execute('PRAGMA table_info(growth_personality_evidence)')}
            if 'delta' not in columns:self.db.execute('ALTER TABLE growth_personality_evidence ADD COLUMN delta REAL NOT NULL DEFAULT 1')
            if 'excerpt' not in columns:self.db.execute("ALTER TABLE growth_personality_evidence ADD COLUMN excerpt TEXT NOT NULL DEFAULT ''")
        self.migrate()
        self.migrate_personality_evidence()

    @staticmethod
    def trait_key(category, topic):
        value=unicodedata.normalize('NFKC',str(topic or '')).casefold()
        value=re.sub(r'[\W_]+','',value,flags=re.UNICODE)[:96]
        return (str(category or '')+':'+value) if value else ''

    def trait_locked(self,topic,*,account=None,role=None):
        account=self.growth.config.settings.account_id if account is None else account
        role=self.growth.hub.role() if role is None else role
        target=self.trait_key('topic',topic)
        if not target:return False
        return any(self.trait_key('topic',row['topic'])==target for row in self.db.execute('''
            SELECT i.topic FROM growth_insights i JOIN memories m ON m.id=i.memory_id
            WHERE m.account=? AND m.role_id=? AND m.chat=? AND m.source_class='internet_learning'
                AND m.locked=1 AND i.category IN ('interest','persona')''',(account,role,INTERNET)))

    def migrate_personality_evidence(self):
        """Import old provenance without treating a single old row as mature."""
        rows=self.db.execute('''SELECT i.memory_id,i.category,i.topic,i.journal_id,m.account,m.role_id,
            e.excerpt,j.sources,j.created AS journal_created,old.delta AS legacy_delta
            FROM growth_insights i JOIN memories m ON m.id=i.memory_id
            JOIN growth_journals j ON j.id=i.journal_id LEFT JOIN memory_evidence e ON e.memory_id=m.id
            LEFT JOIN growth_interest_events old ON old.memory_id=m.id
            WHERE i.category IN ('interest','persona')''').fetchall()
        moved=0
        for raw in rows:
            row=dict(raw)
            try:source_ids=json.loads(row['sources'] or '[]')
            except (TypeError,ValueError):source_ids=[]
            key=self.trait_key(row['category'],row['topic'])
            if not key:continue
            if self.trait_locked(row['topic'],account=row['account'],role=row['role_id']):continue
            delta=row['legacy_delta']
            if not isinstance(delta,(int,float)) or not math.isfinite(delta):delta=1.0
            delta=max(-2.0,min(2.0,float(delta)))
            stance='challenge' if delta<0 else 'support'
            for identity in source_ids:
                source=self.db.execute('SELECT * FROM growth_sources WHERE id=? AND account=? AND role_id=?',
                    (identity,row['account'],row['role_id'])).fetchone()
                if not source:continue
                # Existing evidence is authoritative, including challenges. The
                # legacy uniqueness key includes stance, so INSERT OR IGNORE
                # alone would manufacture a supporting vote on every restart.
                with self.store.lock,self.db:
                    cur=self.db.execute('''INSERT OR IGNORE INTO growth_personality_evidence
                        (account,role_id,trait_key,category,topic,memory_id,journal_id,source_id,source_kind,
                         stance,excerpt_hash,created,delta,excerpt)
                        SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,? WHERE NOT EXISTS (
                            SELECT 1 FROM growth_personality_evidence
                            WHERE account=? AND role_id=? AND trait_key=? AND journal_id=?)''',
                        (row['account'],row['role_id'],key,row['category'],row['topic'],row['memory_id'],
                         row['journal_id'],identity,source_kind(dict(source)),stance,
                         hashlib.sha256(str(row['excerpt'] or '').encode()).hexdigest(),
                         float(row['journal_created'] or time.time()),delta,str(row['excerpt'] or ''),
                         row['account'],row['role_id'],key,row['journal_id']))
                moved+=cur.rowcount
        return moved

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
        permission=self.db.execute('''SELECT p.*,i.category,i.topic FROM growth_memory_policy p
            JOIN growth_insights i ON i.memory_id=p.memory_id WHERE p.memory_id=?''',(row['id'],)).fetchone()
        if not permission:return False
        if permission['category'] in ('interest','persona'):
            state=self.personality_state(permission['category'],permission['topic'])
            return bool(getattr(self.growth.config.settings,'growth_mature_traits_chat_enabled',False)
                and permission['chat_enabled']
                and state and state['status'] in ('stable','stable_challenged')
                and self._live_trait_evidence(permission['category'],permission['topic']))
        if permission['category']=='expression':
            from .conversation import _UNCLE_SLANG_RE
            if _UNCLE_SLANG_RE.search(str(row.get('content') or '') + ' ' + str(row.get('examples') or '')):
                return False
        return bool(permission['chat_enabled'] and
                    (permission['category']!='expression' or self.expression_switch(permission['source_kind'])) and self.source_live(row['id']))

    def register_personality_evidence(self,identity,category,topic,journal_id,source,excerpt,stance='support',delta=1.0):
        if category not in ('interest','persona') or not isinstance(source,dict):return None
        if stance not in ('support','challenge'):stance='support'
        key=self.trait_key(category,topic)
        if not key:return None
        account=self.growth.config.settings.account_id;role=self.growth.hub.role();created=time.time()
        digest=hashlib.sha256(str(excerpt or '').encode()).hexdigest()
        if isinstance(delta,bool) or not isinstance(delta,(int,float)) or not math.isfinite(delta):delta=1.0
        delta=max(-2.0,min(2.0,float(delta)))
        if stance=='challenge':delta=-abs(delta)
        with self.store.lock,self.db:
            memory=self.db.execute('SELECT locked FROM memories WHERE id=? AND account=? AND role_id=?',
                (identity,account,role)).fetchone()
            if not memory or memory['locked'] or self.trait_locked(topic):return None
            self.db.execute('''INSERT OR IGNORE INTO growth_personality_evidence
                (account,role_id,trait_key,category,topic,memory_id,journal_id,source_id,source_kind,stance,excerpt_hash,created,delta,excerpt)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (account,role,key,category,clean(topic,100),identity,journal_id,source['id'],
                 source_kind(source),stance,digest,created,delta,clean(str(excerpt or ''),1000)))
        return self.personality_state(category,topic)

    def _live_trait_evidence(self,category,topic):
        key=self.trait_key(category,topic)
        if not key:return []
        account=self.growth.config.settings.account_id;role=self.growth.hub.role()
        rows=self.db.execute('''SELECT e.*,m.status,m.archived,m.locked,m.chat,m.tier,
                p.learning_enabled,p.chat_enabled,j.sources FROM growth_personality_evidence e
            JOIN memories m ON m.id=e.memory_id
            JOIN growth_memory_policy p ON p.memory_id=m.id
            JOIN growth_journals j ON j.id=e.journal_id
            WHERE e.account=? AND e.role_id=? AND e.trait_key=? AND m.account=? AND m.role_id=?''',
            (account,role,key,account,role)).fetchall()
        result=[]
        for raw in rows:
            row=dict(raw)
            # A lock prevents automatic edits; it does not erase the experience
            # that already supports this trait.
            if not row['learning_enabled'] or row['archived'] or row['status']!='active' or row['tier']=='cold':continue
            try:source_ids=json.loads(row['sources'] or '[]')
            except (TypeError,ValueError):source_ids=[]
            if row['source_id'] not in source_ids:continue
            source=self.db.execute('SELECT kind,url FROM growth_sources WHERE id=? AND account=? AND role_id=?',
                (row['source_id'],account,role)).fetchone()
            if not source or source_kind(dict(source))!=row['source_kind']:continue
            if row['source_kind']=='bilibili' and self.db.execute('SELECT 1 FROM bilibili_readings WHERE source_id=? AND enabled=0',(row['source_id'],)).fetchone():continue
            if row['source_kind']=='tieba':
                match=re.search(r'/p/(\d+)',source['url'])
                if match and self.db.execute('SELECT 1 FROM tieba_lifecycle WHERE account=? AND tid=? AND (trashed_at>0 OR deleted_at>0)',
                        (account,match[1])).fetchone():continue
            row['content_key']=content_key(dict(source))
            result.append(row)
        return result

    @staticmethod
    def _independent_trait_evidence(evidence):
        """Keep all raw history, but give one content one vote per stance."""
        result=[];seen=set()
        for row in sorted(evidence,key=lambda item:(item['created'],item['id'])):
            identity=(row.get('content_key'),row['stance'])
            if not identity[0] or identity in seen:continue
            seen.add(identity);result.append(row)
        return result

    def personality_state(self,category,topic,*,_evidence=None):
        key=self.trait_key(category,topic)
        if not key:return None
        evidence=self._live_trait_evidence(category,topic) if _evidence is None else _evidence
        independent=self._independent_trait_evidence(evidence)
        supports=[row for row in independent if row['stance']=='support']
        challenges=[row for row in independent if row['stance']=='challenge']
        sources={row['content_key'] for row in supports}
        # Maturity means sustained supporting experience. A distant counterexample
        # must not manufacture the six-hour support span by itself.
        first=min((row['created'] for row in supports),default=0)
        last=max((row['created'] for row in supports),default=0)
        prev=self.db.execute('SELECT * FROM growth_personality_states WHERE account=? AND role_id=? AND trait_key=?',
            (self.growth.config.settings.account_id,self.growth.hub.role(),key)).fetchone()
        old=dict(prev) if prev else {}
        if not evidence:status='withdrawn'
        elif (old.get('status') in ('stable','stable_challenged','challenged','weakened') and len(challenges)>=3
                and len({row['content_key'] for row in challenges})>=2 and len(challenges)>len(supports)):
            status='weakened'
        elif (len(supports)>=3 and len(sources)>=2 and last-first>=6*3600
                and len(supports)>=3+2*len(challenges)):
            status='stable'
        elif (old.get('status') in ('stable','stable_challenged') and len(supports)>=3
                and len(sources)>=2 and last-first>=6*3600):status='stable_challenged'
        elif challenges:status='challenged'
        elif len(supports)>=2:status='emerging'
        else:status='candidate'
        current={'account':self.growth.config.settings.account_id,'role_id':self.growth.hub.role(),
            'trait_key':key,'category':category,'topic':clean(topic,100),'status':status,
            'support_count':len(supports),'challenge_count':len(challenges),'source_count':len(sources),
            'first_seen':first,'last_seen':last,'updated':time.time()}
        before={name:old.get(name) for name in ('category','topic','status','support_count','challenge_count','source_count','first_seen','last_seen')}
        after={name:current[name] for name in before}
        if old and before==after:
            current['updated']=old['updated']
            return current
        with self.store.lock,self.db:
            self.db.execute('''INSERT INTO growth_personality_states VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(account,role_id,trait_key) DO UPDATE SET category=excluded.category,topic=excluded.topic,
                status=excluded.status,support_count=excluded.support_count,challenge_count=excluded.challenge_count,
                source_count=excluded.source_count,first_seen=excluded.first_seen,last_seen=excluded.last_seen,updated=excluded.updated''',
                tuple(current.values()))
            if before!=after:
                reason=f"有效证据变化：支持{len(supports)}条、反证{len(challenges)}条、来源{len(sources)}个"
                self.db.execute('''INSERT INTO growth_personality_revisions
                    (account,role_id,trait_key,before_json,after_json,reason,created) VALUES(?,?,?,?,?,?,?)''',
                    (current['account'],current['role_id'],key,json.dumps(before,ensure_ascii=False),
                     json.dumps(after,ensure_ascii=False),reason,current['updated']))
        return current

    def personality_states(self,limit=40,evidence_limit=8,*,with_metadata=False):
        limit=min(40,max(1,int(limit)));evidence_limit=min(8,max(1,int(evidence_limit)))
        scope=(self.growth.config.settings.account_id,self.growth.hub.role())
        total=self.db.execute('SELECT count(DISTINCT trait_key) FROM growth_personality_evidence WHERE account=? AND role_id=?',scope).fetchone()[0]
        keys=self.db.execute('''SELECT trait_key,category,topic,MAX(created) AS last_evidence FROM growth_personality_evidence
            WHERE account=? AND role_id=? GROUP BY trait_key ORDER BY last_evidence DESC,trait_key LIMIT ?''',
            (*scope,limit)).fetchall()
        result=[]
        for row in keys:
            evidence=self._live_trait_evidence(row['category'],row['topic'])
            state=self.personality_state(row['category'],row['topic'],_evidence=evidence)
            if not state:continue
            state['evidence_total']=len(evidence);state['evidence_truncated']=len(evidence)>evidence_limit
            evidence=sorted(evidence,key=lambda item:(item['created'],item['id']),reverse=True)[:evidence_limit]
            state['evidence']=[{'memory_id':item['memory_id'],'journal_id':item['journal_id'],
                'source_id':item['source_id'],'source_kind':item['source_kind'],'content_key':item['content_key'],'stance':item['stance'],
                'created':item['created'],'excerpt':item['excerpt']} for item in evidence]
            result.append(state)
        return ({'items':result,'total':total,'truncated':total>len(result),'limit':limit,'evidence_limit':evidence_limit}
            if with_metadata else result)

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

    def chat_candidates(self,query):
        """Keep a recent window without ageing out explicitly relevant traits."""
        from .topic_relevance import topic_terms
        terms=sorted(topic_terms(query),key=lambda term:(-len(term),term))[:32]
        identities=[]
        if terms and getattr(self.growth.config.settings,'growth_mature_traits_chat_enabled',False):
            # Search only compact trait metadata first. Full memories and their
            # live source permissions are hydrated below, at most 240 in total.
            patterns=['%'+term.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%' for term in terms]
            where=' OR '.join("lower(topic) LIKE ? ESCAPE '\\'" for _ in patterns)
            traits=self.db.execute('''SELECT trait_key,category,topic,MAX(created) AS last_evidence
                FROM growth_personality_evidence WHERE account=? AND role_id=?
                    AND category IN ('interest','persona') AND ('''+where+''')
                GROUP BY trait_key HAVING SUM(stance='support')>=3
                ORDER BY last_evidence DESC,trait_key LIMIT 12''',
                (self.growth.config.settings.account_id,self.growth.hub.role(),*patterns)).fetchall()
            for trait in traits:
                evidence=self._live_trait_evidence(trait['category'],trait['topic'])
                state=self.personality_state(trait['category'],trait['topic'],_evidence=evidence)
                if not state or state['status'] not in ('stable','stable_challenged'):continue
                selected=0
                for item in sorted(evidence,key=lambda row:(row['created'],row['id']),reverse=True):
                    identity=item['memory_id']
                    if item['stance']!='support' or not item['chat_enabled'] or identity in identities:continue
                    identities.append(identity);selected+=1
                    if selected>=2:break
        recent=self.rows(chat=True,limit=240-len(identities))
        if not identities:return recent
        historical=self.rows(chat=True,identities=identities,limit=len(identities))
        seen={row['id'] for row in recent}
        return recent+[row for row in historical if row['id'] not in seen]

    def rows(self,*,include_inactive=False,learning=False,chat=False,source='',before=0,limit=240,identities=None):
        s=self.growth.config.settings
        result=[]
        where="m.account=? AND m.role_id=? AND m.chat=? AND m.source_class='internet_learning'"
        args=[s.account_id,self.growth.hub.role(),INTERNET]
        if identities is not None:
            identities=list(dict.fromkeys(value for value in identities if isinstance(value,int) and not isinstance(value,bool) and value>0))[:240]
            if not identities:return []
            where+=' AND m.id IN ('+','.join('?' for _ in identities)+')';args.extend(identities)
        if source:where+=' AND p.source_kind=?';args.append(source)
        if before:where+=' AND m.id<?';args.append(before)
        if learning:where+=' AND p.learning_enabled=1'
        if chat:where+=" AND (p.chat_enabled=1 OR i.category IN ('interest','persona'))"
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

    def register(self,identity,kind,category,item,*,evidence_verified=False):
        scene=clean(item.get('scene'),120);emotion=clean(item.get('emotion'),60);intent=clean(item.get('intent'),100)
        examples=[clean(value,60) for value in (item.get('examples') or [])[:3] if isinstance(value,str)] if isinstance(item.get('examples'),list) else []
        expression=category=='expression' and bool(scene and emotion and intent and examples)
        projected=' '.join([clean(item.get('text'),140),scene,emotion,intent,*examples,
            clean(item.get('conditions'),160),clean(item.get('counterexamples'),160)])
        from .conversation import _UNCLE_SLANG_RE
        uncle_slang_hit=bool(category=='expression' and _UNCLE_SLANG_RE.search(projected))
        # No credentials, contact/identity strings or links cross via examples.
        unsafe=bool(POISON_PATTERN.search(projected) or uncle_slang_hit or re.search(
            r'https?://|wxid_|@chatroom|\bsk-|\b[A-Za-z_]{12,}\b|\d{5,}|\S+@\S+\.\S+',projected,re.I))
        # Existing permissions stay unchanged. Newly generated expressions need
        # verified context/reaction evidence before automatic sharing eligibility;
        # the existing admin controls can still authorize a reviewed item.
        shared=(expression and evidence_verified or category in ('interest','persona')) and not unsafe and kind in ('bilibili','tieba')
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
        topics={};account=self.growth.config.settings.account_id;role=self.growth.hub.role()
        memories=self.rows(learning=True,include_inactive=True)
        visible_traits={self.trait_key(row['category'],row['topic']) for row in memories if row['category'] in ('interest','persona')}
        candidates=self.db.execute('''SELECT DISTINCT category,topic FROM growth_personality_evidence
            WHERE account=? AND role_id=? ORDER BY topic''',(account,role)).fetchall()
        for candidate in candidates:
            category,topic=candidate['category'],candidate['topic']
            if self.trait_key(category,topic) not in visible_traits:continue
            evidence=self._live_trait_evidence(category,topic)
            state=self.personality_state(category,topic,_evidence=evidence)
            if not evidence or not state or state['status']=='withdrawn':continue
            memory_ids={row['memory_id'] for row in evidence}
            memory=next((row for row in memories
                         if row['id'] in memory_ids and row['category']==category),None)
            if not memory:continue
            weight={'stable':1.0,'stable_challenged':.8,'challenged':.55,'weakened':.2,'emerging':.65,'candidate':.35}.get(state['status'],.35)
            independent=self._independent_trait_evidence(evidence)
            strength=sum(row['delta']*.5**(max(0,time.time()-row['created'])/(21*86400)) for row in independent)*weight
            topics[topic]={'topic':topic,'strength':round(max(-5,min(5,strength)),2),
                'encounters':len(evidence),'thought':memory['content'],'memory_id':memory['id'],
                'last_change':state['last_seen'],'sources':sorted({row['source_kind'] for row in evidence}),
                'maturity':state['status'],'support_count':state['support_count'],
                'challenge_count':state['challenge_count'],'source_count':state['source_count']}
        return sorted(topics.values(),key=lambda value:(-value['strength'],-value['last_change']))[:12]
