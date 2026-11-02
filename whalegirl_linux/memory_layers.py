"""Version-linked shallow summaries over the existing canonical memory store."""
import json
import math
import time
import uuid


WORLD='@world-learning'


class MemoryLayers:
    def __init__(self,hub):
        self.hub,self.store,self.db=hub,hub.store,hub.db
        with self.store.lock,self.db:
            self.db.executescript('''CREATE TABLE IF NOT EXISTS memory_summaries(
                id TEXT PRIMARY KEY,account TEXT NOT NULL,role_id TEXT NOT NULL,chat TEXT NOT NULL,
                subject TEXT NOT NULL,scope TEXT NOT NULL,kind TEXT NOT NULL,venue TEXT NOT NULL,
                summary TEXT NOT NULL,members TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,
                updated REAL NOT NULL,version INTEGER NOT NULL DEFAULT 1);
                CREATE INDEX IF NOT EXISTS memory_summary_scope ON memory_summaries(account,role_id,state);
            ''')

    @staticmethod
    def scope(row):
        # Role-independent real facts can still be read with another card.
        return tuple(row.get(k,'') for k in ('account','role_id','chat','subject','scope','kind','venue'))

    def valid(self,row):
        try:members=json.loads(row['members'])
        except (ValueError,TypeError):return []
        if not isinstance(members,dict) or len(members)<2:return []
        result=[]
        for identity,version in members.items():
            member=self.db.execute('SELECT * FROM memories WHERE id=? AND account=?',
                (identity,row['account'])).fetchone()
            if not member:return []
            member=dict(member)
            if (member['version']!=version or not self.hub.book.active(member)
                    or member['status']!='active' or self.scope(member)!=self.scope(row)):
                return []
            result.append(member)
        return result

    def save(self,account,groups,offered,*,allowed):
        """Only the supplied, unchanged same-scope memories may support a summary."""
        offered={r['id']:r for r in offered};saved=[]
        for group in groups[:4] if isinstance(groups,list) else []:
            if not isinstance(group,dict):continue
            ids=group.get('ids');summary=group.get('summary')
            if (not isinstance(ids,list) or any(not isinstance(v,int) or isinstance(v,bool) for v in ids)
                    or not isinstance(summary,str) or not 4<=len(summary.strip())<=180):continue
            ids=sorted(set(ids))
            if not 2<=len(ids)<=8 or any(i not in offered for i in ids):continue
            members=[offered[i] for i in ids]
            if len({self.scope(r) for r in members})!=1:continue
            first=members[0]
            if first['chat'] not in allowed or first['chat']==WORLD or any(r['locked'] or r['tier']=='core' for r in members):continue
            row={**first,'members':json.dumps({str(r['id']):r['version'] for r in members})}
            with self.store.lock,self.db:
                if not self.valid(row):continue
                previous=self.db.execute('SELECT * FROM memory_summaries WHERE account=? AND members=? AND state=?',
                    (account,row['members'],'active')).fetchone()
                if previous:continue
                identity=uuid.uuid4().hex;now=time.time()
                self.db.execute('INSERT INTO memory_summaries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1)',
                    (identity,*self.scope(first),summary.strip(),row['members'],'active',now,now))
                saved.append(identity)
        return saved

    def candidates(self,settings):
        if not settings.allowed_chat_ids:return []
        marks=','.join('?' for _ in settings.allowed_chat_ids)
        rows=[dict(r) for r in self.db.execute(f'''SELECT * FROM memories WHERE account=? AND chat IN ({marks})
            AND status='active' AND archived=0 AND locked=0 AND tier IN ('stable','ordinary')
            AND (role_id='' OR role_id=?) AND (expires_at=0 OR expires_at>?)
            ORDER BY updated DESC,id DESC LIMIT 160''',
            (settings.account_id,*settings.allowed_chat_ids,self.hub.role(),time.time()))]
        covered=set()
        for row in self.db.execute("SELECT * FROM memory_summaries WHERE account=? AND state='active'",(settings.account_id,)):
            covered.update(r['id'] for r in self.valid(dict(row)))
        groups={}
        for row in rows:
            if row['id'] not in covered:groups.setdefault(self.scope(row),[]).append(row)
        # One scope per batch keeps people, rooms and historical contradictions separate.
        choices=[r for r in groups.values() if len(r)>=2]
        return max(choices,key=lambda rows:(len(rows),max(r['updated'] for r in rows)),default=[])[:12]

    def shallow(self,rows):
        settings=self.hub.settings
        if not settings or not settings.growth_enabled or not rows:return rows
        available={r['id']:r for r in rows};replacements={};consumed=set()
        for raw in self.db.execute("SELECT * FROM memory_summaries WHERE account=? AND state='active' ORDER BY id",
                (rows[0]['account'],)):
            summary=dict(raw);members=self.valid(summary);ids={r['id'] for r in members}
            if not ids or not ids<=available.keys() or ids&consumed:continue
            representative=min(ids)
            replacements[representative]={**available[representative],'content':summary['summary'],
                'summary_id':summary['id'],'detail_ids':sorted(ids),'shallow':True}
            consumed|=ids
        return [replacements.get(r['id'],r) for r in rows
                if r['id'] not in consumed or r['id'] in replacements]

    def expand(self,row,allowed,chat,subjects,shared):
        settings=self.hub.settings
        if not row or chat is None or not settings or not settings.growth_enabled:return row
        for raw in self.db.execute("SELECT * FROM memory_summaries WHERE account=? AND state='active' ORDER BY updated DESC",
                (row['account'],)):
            summary=dict(raw);members=self.valid(summary)
            if row['id'] not in {r['id'] for r in members}:continue
            if any(m['chat'] not in allowed or not self.hub.visible(m,chat,subjects,shared) for m in members):continue
            return {**row,'shallow_summary':summary['summary'],'related_details':[
                {k:m.get(k) for k in ('id','version','subject','content','kind','source_key','created','updated')}
                for m in members]}
        return row

    def snapshot(self,settings):
        result=[]
        for raw in self.db.execute('SELECT * FROM memory_summaries WHERE account=? ORDER BY created DESC LIMIT 30',
                (settings.account_id,)):
            row=dict(raw)
            if row['chat'] not in settings.allowed_chat_ids or row['role_id'] not in ('',self.hub.role()):continue
            row['valid']=bool(self.valid(row));row['detail_ids']=list(map(int,json.loads(row['members'])))
            row.pop('members');result.append(row)
        return result


def usage_boost(row):
    """Bounded retrieval accessibility, never a confidence or truth promotion."""
    return min(2,math.log1p(max(0,row.get('use_count',0)))*.4)
