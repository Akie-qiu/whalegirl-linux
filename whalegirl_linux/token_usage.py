"""Persistent API usage, containing numeric metadata only, never chat content."""
from datetime import datetime, timedelta, timezone
import json
import math
import time
import uuid
from .usage_context import CURRENT_USAGE
from .usage_pricing import quote

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
    details=details if isinstance(details,dict) else {}
    completion=completion if isinstance(completion,dict) else {}
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
    reasoning=count(completion.get('reasoning_tokens'))
    if reasoning is None:reasoning=count(raw.get('reasoning_tokens'))
    # Reasoning is already part of completion/output. A configured thinking
    # budget is a limit, never evidence of tokens consumed or an extra charge.
    result=dict(zip(FIELDS,(prompt,output,total,cached,miss,write,reasoning)))
    details=raw.get('cost_details') if isinstance(raw.get('cost_details'),dict) else {}
    cost=raw.get('cost',details.get('total_cost'))
    if isinstance(cost,(int,float)) and not isinstance(cost,bool) and math.isfinite(cost) and cost>=0:
        result.update(cost_value=cost,cost_currency=str(raw.get('currency') or details.get('currency') or 'provider_units')[:24])
    return result


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
                CREATE TABLE IF NOT EXISTS model_token_details(id TEXT PRIMARY KEY,
                    estimated_fields TEXT NOT NULL DEFAULT '[]',cost_value REAL,
                    cost_currency TEXT NOT NULL DEFAULT '',cost_estimated INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS model_usage_context(id TEXT PRIMARY KEY,
                    account TEXT NOT NULL,chat TEXT NOT NULL DEFAULT '',decision_id TEXT NOT NULL DEFAULT '',
                    stage TEXT NOT NULL DEFAULT '',pricing_json TEXT NOT NULL DEFAULT '{}');
                CREATE INDEX IF NOT EXISTS model_usage_context_chat ON model_usage_context(account,chat);
            ''')
            store.db.execute('INSERT OR IGNORE INTO model_token_meta VALUES(?,?)',('started_at',time.time()))
            columns={row[1] for row in store.db.execute('PRAGMA table_info(model_token_usage)')}
            # Keep the original 15-column table compatible with old releases'
            # positional INSERT. Optional metadata lives in a one-to-one sidecar.
            if {'estimated_fields','cost_value','cost_currency','cost_estimated'}<=columns:
                store.db.execute('INSERT OR IGNORE INTO model_token_details SELECT id,estimated_fields,cost_value,cost_currency,cost_estimated FROM model_token_usage')
            store.db.commit()

    def record(self,account,event):
        values=[event.get(k) for k in FIELDS]
        identity=event.get('id') or uuid.uuid4().hex
        try:
            price=quote(event)
        except (TypeError,ValueError,OverflowError):
            price={'status':'unknown','reason':'计价字段异常，保留原始用量，不猜测金额'}
        with self.store.lock,self.store.db:
            self.store.db.execute('INSERT INTO model_token_usage(id,account,created,provider,model,operation,status,elapsed_ms,'+
                ','.join(FIELDS)+') VALUES('+','.join('?' for _ in range(15))+')',
                (identity,account,event.get('created',time.time()),
                 str(event.get('provider',''))[:200],str(event.get('model',''))[:120],
                 event.get('operation','chat'),event.get('status','error'),int(event.get('elapsed_ms',0)),*values))
            if event.get('estimated_fields') or event.get('cost_value') is not None:
                self.store.db.execute('INSERT INTO model_token_details VALUES(?,?,?,?,?)',
                    (identity,json.dumps(event.get('estimated_fields') or []),event.get('cost_value'),
                     str(event.get('cost_currency') or '')[:24],int(bool(event.get('cost_estimated')))))
            context=CURRENT_USAGE.get()
            context=context if context.get('account')==account else {}
            self.store.db.execute('INSERT INTO model_usage_context VALUES(?,?,?,?,?,?)',
                (identity,account,str(context.get('chat') or '')[:200],context.get('decision_id',''),
                 str(event.get('stage') or event.get('operation') or 'chat')[:100],
                 json.dumps(price,ensure_ascii=False)))
            self._cache.clear()
        return identity

    def snapshot(self,account,period='today',operation_prefix=''):
        now=time.time();day=datetime.fromtimestamp(now,TZ).replace(hour=0,minute=0,second=0,microsecond=0)
        key=(account,period,day.date().isoformat(),operation_prefix)
        with self.store.lock:
            cached=self._cache.get(key)
            if cached and time.monotonic()-cached[0]<10:return cached[1]
        since={'today':day.timestamp(),'7d':(day-timedelta(days=6)).timestamp(),
               '30d':(day-timedelta(days=29)).timestamp(),'all':0}[period]
        where='account=? AND created>=?';args=(account,since)
        if operation_prefix:
            where+=' AND substr(operation,1,?)=?';args=(*args,len(operation_prefix),operation_prefix)
        columns=','.join('sum('+k+') AS '+k+',count('+k+') AS '+k+'_reports' for k in FIELDS)
        aggregates=("count(*) AS requests,sum(status='success') AS successful,sum(status!='success') AS failed,"
            "sum(operation NOT IN ('search','fetch','growth_search','growth_fetch')) AS model_requests,sum(operation IN ('search','fetch','growth_search','growth_fetch')) AS search_requests,"
            "avg(elapsed_ms) AS average_ms,sum(CASE WHEN cached_tokens IS NOT NULL THEN input_tokens ELSE 0 END) AS cache_input,"
            "sum(CASE WHEN total_tokens IS NOT NULL AND instr(coalesce(d.estimated_fields,'[]'),'\"total_tokens\"')=0 THEN 1 ELSE 0 END) AS total_tokens_exact_reports,"+columns)
        joined=' FROM model_token_usage u LEFT JOIN model_token_details d ON d.id=u.id WHERE '
        with self.store.lock:
            started=self.store.db.execute("SELECT value FROM model_token_meta WHERE key='started_at'").fetchone()[0]
            summary=dict(self.store.db.execute('SELECT '+aggregates+joined+where,args).fetchone())
            models=[dict(r) for r in self.store.db.execute('SELECT provider,model,operation,'+aggregates+
                joined+where+' GROUP BY provider,model,operation ORDER BY COALESCE(sum(total_tokens),0) DESC',args)]
            days=[dict(r) for r in self.store.db.execute("SELECT strftime('%Y-%m-%d',created,'unixepoch','+8 hours') AS day,"+
                aggregates+joined+where+' GROUP BY day ORDER BY day DESC LIMIT 30',args)]
            estimates=self.store.db.execute('SELECT count(*)'+joined+where+" AND coalesce(d.estimated_fields,'[]')!='[]'",args).fetchone()[0]
            costs=[dict(r) for r in self.store.db.execute('SELECT d.cost_currency currency,sum(d.cost_value) value,count(*) reports,'
                'sum(d.cost_estimated) estimated_reports'+joined+where+
                ' AND d.cost_value IS NOT NULL GROUP BY d.cost_currency',args)]
        def finish(row):
            for k in ('successful','failed','model_requests','search_requests'):row[k]=row[k] or 0
            row['cache_hit_rate']=round(100*row['cached_tokens']/row['cache_input'],1) if row['cache_input'] and row['cached_tokens'] is not None else None
            row['average_ms']=round(row['average_ms']) if row['average_ms'] is not None else None
            return row
        result={'period':period,'timezone':'Asia/Shanghai','started_at':started,'since':since,
                'summary':finish(summary),'models':[finish(r) for r in models],'days':[finish(r) for r in days],
                'estimated_requests':estimates,'costs':costs,'cost_unreported':summary['requests']-sum(r['reports'] for r in costs)}
        with self.store.lock:self._cache[key]=(time.monotonic(),result)
        return result
