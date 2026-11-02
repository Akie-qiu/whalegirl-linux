"""Three authorized maintenance messages in 临时测试; live DB stays read-only."""
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

ROOT=Path('/opt/whalegirl-linux/current').resolve();sys.path.insert(0,str(ROOT))
from whalegirl_linux.gateway import AgentGateway
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.safety import SafetyGuard
from whalegirl_linux.settings import Config
from whalegirl_linux.store import Store

DATA=Path('/var/lib/whalegirl-linux/core');TARGET='56614122712@chatroom'
JOBS=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/whalegirl-jobs')
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(route,config):
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        headers={'Authorization':'Bearer '+config.admin_token})
    with OPENER.open(request,timeout=8) as response:return json.load(response)


async def idle(config):
    for _ in range(20):
        state=api('state',config)
        if (state.get('paused_reason') or not state.get('enabled') or
                state.get('gateway',{}).get('loggedInUser')!=config.settings.account_id):
            raise RuntimeError('Live bot unavailable; no maintenance send')
        if not state.get('processing'):return
        await asyncio.sleep(.5)
    raise RuntimeError('Live bot busy; no maintenance send')


async def main():
    live=Config(DATA);settings=live.settings
    if TARGET not in settings.allowed_chat_ids:raise RuntimeError('Test chat is not authorized')
    await idle(live)
    out=Path('/opt/whalegirl-linux-assets')/('sticker-cache-live-'+uuid.uuid4().hex[:10]);out.mkdir(mode=0o700)
    config=Config(out);config.settings=settings;config.api_key=live.api_key
    store=Store(out/'state.sqlite3')
    source=sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True);source.row_factory=sqlite3.Row
    os.environ['WHALEGIRL_UI_JOBS_DIR']=str(JOBS)
    gateway=AgentGateway('http://127.0.0.1:6175',live.gateway_token,control_directory=DATA)
    runtime=Runtime(config,store,gateway,ROOT/'styles')
    report={'release':str(ROOT),'target':'临时测试','model_called':False,'rounds':[],'messages_confirmed':0}
    try:
        if not any(c['id']==TARGET and c.get('name')=='临时测试' for c in await gateway.chats()):
            raise RuntimeError('Test chat identity changed')
        runtime.gateway_state=await gateway.status();runtime.account_id=settings.account_id
        guard=object.__new__(SafetyGuard);guard.store=SimpleNamespace(db=source)
        runtime.safety.check=guard.check;runtime.safety.held=guard.held
        order=[r['md5'] for r in (await gateway.native_request('/stickers'))['stickers']]
        excluded={r[0] for r in source.execute('SELECT md5 FROM sticker_library WHERE account=? AND enabled=0',(settings.account_id,))}
        excluded.update(r[0] for r in source.execute('SELECT md5 FROM sticker_chat_library WHERE account=? AND chat=? AND enabled=0',(settings.account_id,TARGET)))
        excluded.update(Store.used_stickers(SimpleNamespace(db=source),settings.account_id,TARGET,settings.sticker_cooldown_seconds))
        candidates=[v for v in order[int(len(order)*.55):] if v not in excluded]
        if len(sys.argv)>1 and sys.argv[1]=='cross-panel':
            def nearest(index,blocked):
                return min((v for v in order if v not in excluded and v not in blocked),
                           key=lambda v:abs(order.index(v)-index))
            end=nearest(int(len(order)*.92),set())
            middle=nearest(29,{end})
            candidates=[end,middle]
            report['native_target_indexes']=[order.index(v) for v in candidates]
        if len(candidates)<2:raise RuntimeError('Not enough unused authorized native stickers')
        for index,md5 in enumerate(candidates[:2]):
            await idle(live)
            text='【维护验收】测一下原生表情的发送速度'
            actions=([{'type':'text','text':text,'display':text}] if index==0 else [])
            actions.append({'type':'sticker','id':md5,'display':'[原生表情] 缓存验收'+str(index+1)})
            first={'key':'maintenance:'+uuid.uuid4().hex,'account':settings.account_id,'chat':TARGET,
                'sender':'维护验收场景','sender_id':'maintenance-test','kind':1,'local_id':0,
                'timestamp':time.time(),'trigger':'maintenance','content':'原生表情缓存计时验收'}
            store.insert(first);started=time.time()
            await runtime.send_plan(first,[first],first,actions,{},config.generation,{})
            row=store.get(first['key']);ledger=store.actions(first['key'])
            count=sum(a['state']=='sent' for a in ledger);report['messages_confirmed']+=count
            native=[]
            for path in JOBS.glob('*-part-*.json'):
                if path.stat().st_mtime<started:continue
                try:value=json.loads(path.read_text())
                except (OSError,ValueError):continue
                if value.get('chat')==TARGET and value.get('md5')==md5:
                    native.append({k:value[k] for k in ('state','favorite_cache','favorite_seconds','favorite_scroll','ui_timing') if k in value})
            report['rounds'].append({'state':row['state'],'timing':runtime.last_timing,'native':native})
            if row['state']!='sent' or count!=len(actions):raise RuntimeError('Maintenance delivery not confirmed; no repeat')
        report['live_config_unchanged']=api('config',live)['enabled']==settings.enabled
    finally:
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        Path('/opt/whalegirl-linux-assets/sticker-cache-live-latest.json').write_text(
            json.dumps({**report,'report_path':str(out/'report.json')},ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k!='rounds'},ensure_ascii=False),flush=True)
        source.close();await runtime.close()


if __name__=='__main__':asyncio.run(main())
