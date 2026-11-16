"""Bounded local synthetic benchmark. Does not connect to production or models."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from whalegirl_linux.store import Store
from whalegirl_linux.token_usage import TokenLedger
from whalegirl_linux.usage_dashboard import UsageDashboard
from whalegirl_linux.usage_pricing import quote


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--requests',type=int,default=50000)
    args=parser.parse_args()
    if not 1<=args.requests<=100000:raise ValueError('Use a bounded sample of 1..100000')
    parent=ROOT/'artifacts/usage-dashboard-20261110'
    folder=Path(tempfile.mkdtemp(prefix='benchmark-',dir=parent))
    store=Store(folder/'state.sqlite3');TokenLedger(store)
    now=time.time()
    sample=dict(created=now,model='deepseek-flash',provider='api.deepseek.com',operation='chat',status='success',
                input_tokens=1000,output_tokens=100,cached_tokens=800)
    price=json.dumps(quote(sample))
    try:
        with store.db:
            for start in range(0,args.requests,2000):
                ids=range(start,min(start+2000,args.requests))
                rows=[(str(i),'account',now-(i%500000),'api.deepseek.com','deepseek-flash','chat','success',
                       1200,1000,100,1100,800,200,None,None) for i in ids]
                store.db.executemany('INSERT INTO model_token_usage VALUES('+','.join('?' for _ in range(15))+')',rows)
                store.db.executemany('INSERT INTO model_usage_context VALUES(?,?,?,?,?,?)',
                    [(str(i),'account','group'+str(i%10)+'@chatroom',str(i),'chat',price) for i in ids])
                store.db.executemany('INSERT INTO outbound_actions(id,inbox_key,account,chat,kind,reply,state,sent_at) '
                    'VALUES(?,?,?,?,?,?,?,?)',[(str(i),str(i),'account','group'+str(i%10)+'@chatroom','text',
                    'synthetic','sent',now-(i%500000)) for i in ids])
                store.db.executemany('INSERT INTO message_log(key,account,chat,sender_id,sender,kind,is_self,local_id,content,timestamp) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?)',[(str(i),'account','group'+str(i%10)+'@chatroom','person'+str(i%20),
                    'synthetic',1,0,i+1,'synthetic',now-(i%500000)) for i in ids])
        runtime=SimpleNamespace(store=store,config=SimpleNamespace(settings=SimpleNamespace(account_id='account')),
                                chat_labels={},chats_cache=[])
        service=UsageDashboard(runtime)
        result=service.snapshot('7d',now=now)
        begun=time.perf_counter();service.snapshot('7d',now=now);cached_ms=(time.perf_counter()-begun)*1000
        service.cache.clear()
        tracemalloc.start()
        instrumented=service.snapshot('7d',now=now)
        _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
        assert result['tokens']['summary']['requests']==args.requests
        assert result['speech']['summary']['sent']==args.requests
        report=dict(environment='Windows local synthetic benchmark; not a Linux server load test',
                    requests=args.requests,sends=args.requests,incoming=args.requests,
                    uncached_query_ms=result['query_ms'],cached_call_ms=round(cached_ms,3),
                    instrumented_query_ms=instrumented['query_ms'],
                    python_peak_mib=round(peak/1024**2,2),
                    memory_note='tracemalloc measures Python allocations, excluding SQLite native page cache',
                    background_tasks=0,real_model_calls=0,wechat_sends=0)
        (parent/'benchmark.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
    finally:store.close()


if __name__=='__main__':main()
