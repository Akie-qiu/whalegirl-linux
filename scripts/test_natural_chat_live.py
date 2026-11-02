"""User-authorized, bounded native demonstration in 临时测试 only.

Uses the new worker with an isolated DB and current read-only model inputs.
No deployment, production Config/Store, private profiles, or background jobs.
Confirmed native actions remain in the normal delivery ledger in the sandbox.
"""
import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
import urllib.request
import uuid

CORE=Path('/var/lib/whalegirl-linux/core')
TARGET='56614122712@chatroom'
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(route):
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        headers={'Authorization':'Bearer '+(CORE/'admin.token').read_text().strip()})
    with OPENER.open(request,timeout=12) as response:return json.load(response)


def protected():
    names=['settings.json','persona-cards.json','model-profiles.json','model-api-keys.enc']
    return {n:hashlib.sha256((CORE/n).read_bytes()).hexdigest() for n in names}


async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--code',type=Path,required=True)
    parser.add_argument('--harness',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();sys.path.insert(0,str(args.code.resolve()))
    from whalegirl_linux.gateway import AgentGateway
    from whalegirl_linux.runtime import Runtime
    from whalegirl_linux.safety import SafetyGuard
    from whalegirl_linux.settings import Config,Settings
    from whalegirl_linux.store import Store
    spec=importlib.util.spec_from_file_location('read_only_inputs',args.harness)
    harness=importlib.util.module_from_spec(spec);spec.loader.exec_module(harness)
    raw,persona,profile,key,stickers=harness.production_inputs(None)
    if TARGET not in raw.get('allowed_chat_ids',[]):raise RuntimeError('Target not authorized')
    args.out.mkdir(parents=True,exist_ok=True)
    folder=Path(tempfile.mkdtemp(prefix='native-demo-',dir=args.out))
    before=protected();os.environ.pop('WHALEGIRL_UI_JOBS_DIR',None)
    for name,value in stickers.items():(folder/name).write_text(json.dumps(value,ensure_ascii=False),'utf-8')
    values={k:v for k,v in raw.items() if k in Settings.model_fields}
    values.update({k:profile[k] for k in ('api_url','api_style','model','max_tokens','reasoning_effort','model_stream_enabled') if k in profile})
    # Synthetic messages have no native quote/mention target. This demo only
    # exercises text/sticker participation; all changes stay in the sandbox.
    values.update(system_prompt_override=persona,allowed_chat_ids=[TARGET],trigger_only_chat_ids=[],
        reply_max_parts=min(2,raw.get('reply_max_parts',2)),sticker_max_per_reply=1,
        quote_replies_enabled=False,group_mentions_enabled=False,learning_enabled=False,
        memory_auto_capture=False,followup_wait_enabled=False,proactive_enabled=False,
        images_chat_enabled=False,tieba_enabled=False,moments_enabled=False,web_search_enabled=False)
    config=Config(folder);config.save(Settings.model_validate(values),'sandbox-placeholder')
    config.api_key=lambda profile_id=None:key
    os.environ['WHALEGIRL_UI_JOBS_DIR']='/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/whalegirl-jobs'
    gateway=AgentGateway('http://127.0.0.1:6175',(CORE/'gateway.token').read_text().strip(),control_directory=CORE)
    store=Store(folder/'state.sqlite3');runtime=Runtime(config,store,gateway,args.code/'styles')
    db=sqlite3.connect((CORE/'state.sqlite3').as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    s=config.settings;report={'target':'临时测试','code':str(args.code),'sandbox':str(folder),'rounds':[],
        'synthetic_inputs':True,'production_memory_writes':False,'deployed':False}
    calls=[];completed=False
    async def idle():
        for _ in range(20):
            state=api('state')
            if (not state.get('enabled') or state.get('paused_reason') or
                state.get('gateway',{}).get('status')!='logged_in' or
                state.get('gateway',{}).get('loggedInUser')!=s.account_id):
                raise RuntimeError('Live account unavailable or paused; no further sends')
            inflight=any(db.execute(query).fetchone() for query in (
                "SELECT 1 FROM outbound_actions WHERE state='sending' LIMIT 1",
                "SELECT 1 FROM picture_jobs WHERE state='sending' LIMIT 1",
                "SELECT 1 FROM moments_actions WHERE state='sending' LIMIT 1"))
            if not state.get('processing') and not inflight:return
            await asyncio.sleep(.5)
        raise RuntimeError('Live worker busy; no further sends')
    original_send,original_sticker=gateway.send,gateway.send_sticker
    async def send(chat,text,**options):
        if chat!=TARGET:raise RuntimeError('Out-of-scope send')
        await idle();return await original_send(chat,text,**options)
    async def send_sticker(chat,md5):
        if chat!=TARGET:raise RuntimeError('Out-of-scope sticker')
        await idle();return await original_sticker(chat,md5)
    gateway.send,gateway.send_sticker=send,send_sticker
    try:
        await idle()
        if not any(c['id']==TARGET and c.get('name')=='临时测试' for c in await gateway.chats()):
            raise RuntimeError('Native group identity mismatch')
        runtime.gateway_state=await gateway.status();runtime.account_id=s.account_id
        # Only already accepted room expression references and their exact
        # evidence are copied. No cross-chat personal profiles or chat dump.
        rows=db.execute("SELECT * FROM memories WHERE account=? AND chat=? AND kind IN ('chat_style','expression') AND status='active' AND archived=0 AND (role_id='' OR role_id=?) ORDER BY updated DESC LIMIT 8",
            (s.account_id,TARGET,runtime.memory_hub.role())).fetchall()
        def copy(table,items):
            for row in items:
                names=list(row.keys());store.db.execute('INSERT OR REPLACE INTO '+table+'('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')',tuple(row))
        with store.lock,store.db:
            copy('memories',rows)
            for row in rows:
                evidence=db.execute('SELECT * FROM memory_evidence WHERE memory_id=? AND account=?',(row['id'],s.account_id)).fetchall()
                copy('memory_evidence',evidence)
                for evidence_row in evidence:
                    copy('message_log',db.execute('SELECT * FROM message_log WHERE account=? AND chat=? AND key=?',
                        (s.account_id,TARGET,evidence_row['source_key'])).fetchall())
                # Preserve source withdrawals used by the normal selector.
                for suppression in db.execute('SELECT * FROM memory_suppressions WHERE account=? AND active=1 AND source_key IN (SELECT source_key FROM memory_evidence WHERE memory_id=?)',(s.account_id,row['id'])).fetchall():
                    copy('memories',db.execute('SELECT * FROM memories WHERE id=?',(suppression['memory_id'],)).fetchall())
                    copy('memory_suppressions',[suppression])
        await runtime.stickers.refresh_available(gateway)
        runtime.stickers.disabled={r[0] for r in db.execute('SELECT md5 FROM sticker_library WHERE account=? AND enabled=0',(s.account_id,))}
        excluded={r[0] for r in db.execute('SELECT md5 FROM sticker_chat_library WHERE account=? AND chat=? AND enabled=0',(s.account_id,TARGET))}
        excluded.update(Store.used_stickers(SimpleNamespace(db=db),s.account_id,TARGET,s.sticker_cooldown_seconds))
        runtime.sticker_library.excluded=lambda account,chat:excluded
        live_guard=object.__new__(SafetyGuard);live_guard.store=SimpleNamespace(db=db)
        local_activity=runtime.safety.activity;local_held=runtime.safety.held
        def activity(account,now=None,chat=None):
            a,rows_a,day=live_guard.activity(account,now,chat);b,rows_b,_=local_activity(account,now,chat)
            return {k:a[k]+b[k] for k in a},list(rows_a)+list(rows_b),day
        runtime.safety.activity=activity;runtime.safety.held=lambda account:live_guard.held(account) or local_held(account)
        runtime.last_send=max((r['sent_at'] for r in activity(s.account_id,chat=TARGET)[1]),default=0)
        engine=await runtime.model();chat=engine.chat
        async def recorded(messages,**options):
            started=time.monotonic();result=await chat(messages,**options)
            calls.append({'seconds':round(time.monotonic()-started,3),'returned':bool(result),
                          'finish':getattr(engine,'last_response_meta',{}).get('finish_reason'),'error':engine.last_error})
            return result
        engine.chat=recorded
        prefix=uuid.uuid4().hex
        def event(index,text,kind=1,payload=None,trigger='direct'):
            return dict(key=f'{s.account_id}/{TARGET}/demo:{prefix}:{index}',account=s.account_id,chat=TARGET,
                sender='管理员合成测试',sender_id='maintenance-demo',kind=kind,is_self=0,local_id=1900000000+index,
                timestamp=time.time(),content=text,trigger=trigger,payload_json=json.dumps(payload or {},ensure_ascii=False))
        def result(first):
            row=store.get(first['key']);actions=store.actions(first['key'])
            return {'state':row['state'],'error':row['error'],
                'actions':[{'kind':a['kind'],'state':a['state'],'receipt':a['receipt'],'error':a['error'],'text':a['reply'] if a['kind']=='text' else ''} for a in actions]}
        def echo(first):
            for i,a in enumerate(store.actions(first['key'])):
                if a['state']!='sent':continue
                store.observe([dict(key=f'{s.account_id}/{TARGET}/{a["receipt"]}',account=s.account_id,chat=TARGET,
                    sender='机器人',sender_id=s.account_id,kind=1 if a['kind']=='text' else 47,is_self=1,
                    local_id=1800000000+i+len(report['rounds'])*10,content=a['reply'] if a['kind']=='text' else '[表情包]',
                    timestamp=time.time(),payload_json='{}')])
        intro=event(0,'管理员授权的新版演示')
        notice='【新版聊天测试】\n合成场景①：今天开会开到忘了要说什么，人在工位，魂在放假\n随后补一个赞同表情，看看她会接话、回表情，还是自然收住'
        store.insert(intro)
        await runtime.send_plan(intro,[intro],intro,[{'type':'text','text':notice,'display':notice}],{},config.generation,{})
        report['introduction']=result(intro)
        if report['introduction']['state']!='sent':raise RuntimeError('Introduction not confirmed; stop without resend')
        first=event(1,'今天开会开到忘了要说什么，人在工位，魂在放假')
        store.insert(first);store.observe([first]);started=time.monotonic()
        await asyncio.wait_for(runtime.process_next(),timeout=75)
        report['rounds'].append({'input':'合成工作吐槽','seconds':round(time.monotonic()-started,3),**result(first)})
        echo(first)
        if report['rounds'][-1]['state'] not in ('sent','ignored'):raise RuntimeError('First round incomplete; stop without replay')
        md5=next((k for k,v in runtime.stickers.entries.items() if '点头赞同' in v),None)
        if not md5:raise RuntimeError('No matching nodding reaction fixture')
        signal=runtime.stickers.chat_signal(md5)
        second=event(2,'[原生表情包] 表达：'+signal,47,{'sticker':{'md5':md5}},trigger='natural')
        store.insert(second);store.observe([second]);started=time.monotonic()
        for _ in range(24):
            await asyncio.wait_for(runtime.process_next(),timeout=75)
            if store.get(second['key'])['state']!='queued':break
            await asyncio.sleep(.25)
        report['rounds'].append({'input':'随后补赞同表情','seconds':round(time.monotonic()-started,3),**result(second)})
        completed=True
    finally:
        report.update(completed=completed,model_calls=calls,protected_files_unchanged=protected()==before,
                      confirmed=sum(a['state']=='sent' for item in [report.get('introduction',{}),*report['rounds']] for a in item.get('actions',[])))
        (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
        print(json.dumps({'report':str(folder/'report.json'),'completed':completed,
            'confirmed':report['confirmed'],'protected_files_unchanged':report['protected_files_unchanged'],
            'rounds':[{'state':r['state'],'seconds':r['seconds'],'types':[a['kind'] for a in r['actions']]} for r in report['rounds']]},ensure_ascii=False),flush=True)
        db.close();await runtime.close()


if __name__=='__main__':asyncio.run(main())
