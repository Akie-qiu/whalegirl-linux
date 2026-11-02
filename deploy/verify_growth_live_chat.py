"""One explicitly authorized maintenance message to the 临时测试 group.

Never instantiate Store on the live database. Only public learning rows are
copied to an isolated verification database; production settings stay intact.
The standard send planner and current read-only safety checks are preserved.
"""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from types import SimpleNamespace
import urllib.request
import uuid

ROOT=Path('/opt/whalegirl-linux/current').resolve()
sys.path.insert(0,str(ROOT))
from whalegirl_linux.gateway import AgentGateway
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.safety import SafetyGuard
from whalegirl_linux.settings import Config
from whalegirl_linux.store import Store
from whalegirl_linux.upstream.ai_engine import AIEngine

DATA=Path('/var/lib/whalegirl-linux/core')
TARGET='56614122712@chatroom'
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(route,token):
    req=urllib.request.Request('http://127.0.0.1:8092'+route,headers={'Authorization':'Bearer '+token})
    with OPENER.open(req,timeout=12) as response:return json.load(response)


async def idle(token,account):
    for _ in range(30):
        state=api('/api/state',token)
        if state.get('paused_reason') or not state.get('enabled'):raise RuntimeError('Live bot is paused or disabled')
        if state.get('gateway',{}).get('loggedInUser')!=account:raise RuntimeError('Live account changed')
        if state.get('gateway',{}).get('status')!='logged_in':raise RuntimeError('WeChat is offline')
        if not state.get('processing'):return
        await asyncio.sleep(1)
    raise RuntimeError('Live chat remains busy; no test message sent')


async def main():
    live=Config(DATA);settings=live.settings
    if TARGET not in settings.allowed_chat_ids:raise RuntimeError('Test group is not authorized')
    await idle(live.admin_token,settings.account_id)
    out=Path('/opt/whalegirl-linux-assets')/('growth-chat-test-'+uuid.uuid4().hex[:12])
    out.mkdir(mode=0o700)
    config=Config(out);config.settings=settings;config.api_key=live.api_key
    store=Store(out/'state.sqlite3')
    os.environ['WHALEGIRL_UI_JOBS_DIR']='/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/whalegirl-jobs'
    gateway=AgentGateway('http://127.0.0.1:6175',live.gateway_token,control_directory=DATA)
    runtime=Runtime(config,store,gateway,ROOT/'styles')
    source=sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True)
    source.row_factory=sqlite3.Row
    engine=None;report={'target':'临时测试','actual_messages':0,'release':str(ROOT)}
    try:
        chats=await gateway.chats()
        if not any(c['id']==TARGET and c.get('name')=='临时测试' for c in chats):raise RuntimeError('Test group identity mismatch')
        role=runtime.memory_hub.role()
        memories=source.execute("SELECT * FROM memories WHERE account=? AND role_id=? AND source_class='world_learning' AND status='active' AND archived=0 ORDER BY id DESC LIMIT 12",(settings.account_id,role)).fetchall()
        def copy(table,rows):
            for row in rows:
                names=list(row.keys())
                store.db.execute('INSERT OR REPLACE INTO '+table+'('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')',tuple(row))
        with store.lock,store.db:
            copy('memories',memories)
            ids=[r['id'] for r in memories]
            if ids:
                marks=','.join('?' for _ in ids)
                copy('growth_insights',source.execute('SELECT * FROM growth_insights WHERE memory_id IN ('+marks+')',ids).fetchall())
                copy('memory_evidence',source.execute('SELECT * FROM memory_evidence WHERE memory_id IN ('+marks+')',ids).fetchall())
            else:
                # Include the withdrawn public rows so exclusion is checked by
                # the real selector, rather than by an empty test database.
                withdrawn=source.execute("SELECT * FROM memories WHERE account=? AND role_id=? AND source_class='world_learning' AND archived=1 ORDER BY id DESC LIMIT 12",(settings.account_id,role)).fetchall()
                copy('memories',withdrawn)
                old_ids=[r['id'] for r in withdrawn]
                if old_ids:
                    copy('growth_insights',source.execute('SELECT * FROM growth_insights WHERE memory_id IN ('+','.join('?' for _ in old_ids)+')',old_ids).fetchall())
        question=('你最近读到的绝区零抽卡意愿讨论里，有什么想法？只说一个具体认识，不编造没读过的内容'
                  if memories else '这是管理员授权的通信验收，请自然简短地说一句你能正常聊天了，不编造学习经历')
        first={'key':'maintenance:'+uuid.uuid4().hex,'account':settings.account_id,'chat':TARGET,
               'sender':'系统功能验收','sender_id':'','kind':1,'local_id':0,'timestamp':time.time(),
               'trigger':'maintenance','content':question,'payload_json':'{}'}
        diagnostics={};growth_prompt=runtime.growth.prompt(first,[first],diagnostics)
        if memories and not diagnostics.get('ids'):raise RuntimeError('Learning prompt did not load any memories')
        if not memories and (growth_prompt or diagnostics.get('ids')):raise RuntimeError('Withdrawn learning was incorrectly loaded')
        details=[runtime.memory_book.get(settings.account_id,mid,settings.allowed_chat_ids) for mid in diagnostics.get('ids',[])]
        if not all(details):raise RuntimeError('Deep memory retrieval failed')
        style=settings.system_prompt_override.strip() or runtime.styles.get_active().system_prompt
        system=style+growth_prompt+'\n本次是管理员授权的维护验收，只回答给定问题。自然简短地说一两句，不发工具或动作JSON，不引入私人记忆。'
        engine=AIEngine(api_url=settings.api_url,api_key=live.api_key(),model=settings.model,
            api_style=settings.api_style,reasoning_effort=settings.reasoning_effort,
            stream_enabled=settings.model_stream_enabled,max_tokens=600,temperature=.55,timeout=45,max_retries=0)
        usages=[]
        def usage(event):
            usages.append(event)
            runtime.token_ledger.record(settings.account_id,event)
        engine.usage_callback=usage
        started=time.monotonic()
        text=(await engine.chat([{'role':'user','content':question}],system_prompt=system,max_tokens=600,operation='connection_test') or '').strip()
        if not text or len(text)>600:raise RuntimeError('No suitable bounded test reply')
        if memories and not any(word in text for word in ('意愿','投票','挽昼','立绘','剧情','实际抽')):
            raise RuntimeError('Generated reply did not demonstrate the supplied learning material')
        await idle(live.admin_token,settings.account_id)
        live_guard=object.__new__(SafetyGuard);live_guard.store=SimpleNamespace(db=source)
        runtime.safety.check=live_guard.check;runtime.safety.held=live_guard.held
        payload='【维护收发验收】\n'+text
        guard=live_guard.check(settings.account_id,TARGET,settings,reply=payload,kind='text')
        if not guard['allowed']:raise RuntimeError('Live send safeguard declined the test: '+guard['code'])
        first['timestamp']=time.time();store.insert(first)
        await runtime.send_plan(first,[first],first,[{'type':'text','text':payload,'display':payload}],{},config.generation,{})
        actions=store.actions(first['key']);row=store.get(first['key'])
        report.update(loaded_memory_ids=diagnostics.get('ids',[]),deep_memory_count=len(details),
            archived_learning_excluded=not bool(memories),
            generation_seconds=round(time.monotonic()-started,3),state=row['state'],
            actual_messages=sum(a['state']=='sent' for a in actions),
            actions=[{'state':a['state'],'receipt':a['receipt'],'error':a['error']} for a in actions],
            reply=payload,budget_unchanged=api('/api/config',live.admin_token)['growth_daily_tokens']==settings.growth_daily_tokens,
            usage_events=len(usages),timing=runtime.last_timing)
        if row['state']!='sent':raise RuntimeError('Test delivery was not confirmed; do not resend automatically')
    except Exception as exc:
        report['error']=str(exc)[:300]
        raise
    finally:
        report['report_path']=str(out/'report.json')
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k not in ('reply','timing')},ensure_ascii=False),flush=True)
        if engine:await engine.close()
        source.close();await runtime.close()


if __name__=='__main__':asyncio.run(main())
