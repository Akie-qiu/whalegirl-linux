"""Persistent API usage, containing numeric metadata only, never chat content."""
from datetime import datetime, timedelta, timezone
import time
import uuid

TZ=timezone(timedelta(hours=8))
FIELDS=('input_tokens','output_tokens','total_tokens','cached_tokens','uncached_tokens','cache_write_tokens','reasoning_tokens')


def count(value):
    return value if isinstance(value,int) and not isinstance(value,bool) and value>=0 else None


def normalize_usage(raw,style='openai'):
    raw=raw if isinstance(raw,dict) else {}
    prompt=count(raw.get('prompt_tokens',raw.get('input_tokens')))
    output=count(raw.get('completion_tokens',raw.get('output_tokens')))
    details=raw.get('prompt_tokens_details') or raw.get('input_tokens_details') or {}
    completion=raw.get('completion_tokens_details') or raw.get('output_tokens_details') or {}
    cached=count(raw.get('prompt_cache_hit_tokens',details.get('cached_tokens',raw.get('cache_read_input_tokens'))))
    write=count(raw.get('cache_creation_input_tokens',details.get('cache_write_tokens',details.get('cache_creation_tokens'))))
    miss=count(raw.get('prompt_cache_miss_tokens'))
    if style=='anthropic' and prompt is not None:
        # Anthropic input_tokens excludes both cached reads and cache creation.
        miss=prompt;prompt+= (cached or 0)+(write or 0)
    elif prompt is not None and cached is not None and miss is None:
        miss=max(0,prompt-cached-(write or 0))
    total=count(raw.get('total_tokens'))
    if total is None and prompt is not None and output is not None:total=prompt+output
    return dict(zip(FIELDS,(prompt,output,total,cached,miss,write,count(completion.get('reasoning_tokens')))))


class TokenLedger:
    def __init__(self,store):
        self.store=store
        self._cache={}
        with store.lock:
            store.db.executescript('''
                CREATE TABLE IF NOT EXISTS model_token_usage(
                    id TEXT PRIMARY KEY,account TEXT NOT NULL,created REAL NOT NULL,
                    provider TEXT NOT NULL,model TEXT NOT NULL,operation TEXT NOT NULL,status TEXT NOT NULL,
                    elapsed_ms INTEGER NOT NULL,input_tokens INTEGER,output_tokens INTEGER,total_tokens INTEGER,
                    cached_tokens INTEGER,uncached_tokens INTEGER,cache_write_tokens INTEGER,reasoning_tokens INTEGER);
                CREATE INDEX IF NOT EXISTS model_token_usage_account_time ON model_token_usage(account,created);
                CREATE TABLE IF NOT EXISTS model_token_meta(key TEXT PRIMARY KEY,value REAL NOT NULL);
            ''')
            store.db.execute('INSERT OR IGNORE INTO model_token_meta VALUES(?,?)',('started_at',time.time()))
            store.db.commit()

    def record(self,account,event):
        values=[event.get(k) for k in FIELDS]
        with self.store.lock:
            self.store.db.execute('INSERT INTO model_token_usage VALUES('+','.join('?' for _ in range(15))+')',
                (event.get('id') or uuid.uuid4().hex,account,event.get('created',time.time()),
                 str(event.get('provider',''))[:200],str(event.get('model',''))[:120],
                 event.get('operation','chat'),event.get('status','error'),int(event.get('elapsed_ms',0)),*values))
            self.store.db.commit()
            self._cache.clear()

    def snapshot(self,account,period='today'):
        now=time.time();day=datetime.fromtimestamp(now,TZ).replace(hour=0,minute=0,second=0,microsecond=0)
        key=(account,period,day.date().isoformat())
        with self.store.lock:
            cached=self._cache.get(key)
            if cached and time.monotonic()-cached[0]<10:return cached[1]
        since={'today':day.timestamp(),'7d':(day-timedelta(days=6)).timestamp(),
               '30d':(day-timedelta(days=29)).timestamp(),'all':0}[period]
        where='account=? AND created>=?';args=(account,since)
        columns=','.join('sum('+k+') AS '+k+',count('+k+') AS '+k+'_reports' for k in FIELDS)
        aggregates=("count(*) AS requests,sum(status='success') AS successful,sum(status!='success') AS failed,"
            "sum(operation NOT IN ('search','fetch')) AS model_requests,sum(operation IN ('search','fetch')) AS search_requests,"
            "avg(elapsed_ms) AS average_ms,sum(CASE WHEN cached_tokens IS NOT NULL THEN input_tokens ELSE 0 END) AS cache_input,"+columns)
        with self.store.lock:
            started=self.store.db.execute("SELECT value FROM model_token_meta WHERE key='started_at'").fetchone()[0]
            summary=dict(self.store.db.execute('SELECT '+aggregates+' FROM model_token_usage WHERE '+where,args).fetchone())
            models=[dict(r) for r in self.store.db.execute('SELECT provider,model,operation,'+aggregates+
                ' FROM model_token_usage WHERE '+where+' GROUP BY provider,model,operation ORDER BY COALESCE(sum(total_tokens),0) DESC',args)]
            days=[dict(r) for r in self.store.db.execute("SELECT strftime('%Y-%m-%d',created,'unixepoch','+8 hours') AS day,"+
                aggregates+' FROM model_token_usage WHERE '+where+' GROUP BY day ORDER BY day DESC LIMIT 30',args)]
        def finish(row):
            for k in ('successful','failed','model_requests','search_requests'):row[k]=row[k] or 0
            row['cache_hit_rate']=round(100*row['cached_tokens']/row['cache_input'],1) if row['cache_input'] and row['cached_tokens'] is not None else None
            row['average_ms']=round(row['average_ms']) if row['average_ms'] is not None else None
            return row
        result={'period':period,'timezone':'Asia/Shanghai','started_at':started,'since':since,
                'summary':finish(summary),'models':[finish(r) for r in models],'days':[finish(r) for r in days]}
        with self.store.lock:self._cache[key]=(time.monotonic(),result)
        return result
