"""Authorized 临时测试 worker benchmark; live configuration/inbox are read-only."""
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
from whalegirl_linux.stickers import StickerCatalog
from whalegirl_linux.store import Store

DATA=Path('/var/lib/whalegirl-linux/core');TARGET='56614122712@chatroom'
ASSETS=Path('/opt/whalegirl-linux-assets/fast-turn-20261030')
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path,config):
    request=urllib.request.Request('http://127.0.0.1:8092'+path,headers={'Authorization':'Bearer '+config.admin_token})
    with OPENER.open(request,timeout=12) as response:return json.load(response)


async def main():
    mode=sys.argv[1];fast=mode!='baseline';live=Config(DATA);s=live.settings
    if TARGET not in s.allowed_chat_ids:raise RuntimeError('Test group is not authorized')
    state=api('/api/state',live)
    if state.get('paused_reason') or state.get('processing'):raise RuntimeError('Live bot is paused or busy; test postponed')
    out=ASSETS/(mode+'-'+uuid.uuid4().hex[:10]);out.mkdir(mode=0o700)
    config=Config(out);config.settings=s.model_copy(update={'enabled':True,'fast_reply_enabled':fast,
        'reply_max_parts':2,'sticker_max_per_reply':1,'autonomy_enabled':False,
        'debounce_single_seconds':0,'debounce_burst_seconds':0,'debounce_max_seconds':0})
    config.api_key=live.api_key
    store=Store(out/'state.sqlite3');source=sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True);source.row_factory=sqlite3.Row
    os.environ['WHALEGIRL_UI_JOBS_DIR']='/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/whalegirl-jobs'
    gateway=AgentGateway('http://127.0.0.1:6175',live.gateway_token,control_directory=DATA)
    runtime=Runtime(config,store,gateway,ROOT/'styles')
    report={'mode':mode,'release':str(ROOT),'target':'临时测试','messages_confirmed':0,'live_enabled_before':s.enabled}
    try:
        runtime.gateway_state=await gateway.status();runtime.account_id=s.account_id
        if runtime.gateway_state.get('loggedInUser')!=s.account_id:raise RuntimeError('Account changed')
        if not any(c['id']==TARGET and c.get('name')=='临时测试' for c in await gateway.chats()):raise RuntimeError('Test group identity changed')
        def copy(table,rows):
            for row in rows:
                names=list(row.keys());store.db.execute('INSERT OR REPLACE INTO '+table+'('+','.join(names)+') VALUES('+','.join('?' for _ in names)+')',tuple(row))
        with store.lock,store.db:
            copy('message_log',source.execute('SELECT * FROM message_log WHERE account=? AND chat=? ORDER BY timestamp DESC,local_id DESC LIMIT 40',(s.account_id,TARGET)).fetchall())
            copy('memories',source.execute('SELECT * FROM memories WHERE account=?',(s.account_id,)).fetchall())
        catalog=StickerCatalog(DATA);await catalog.refresh_available(gateway)
        catalog.disabled={r[0] for r in source.execute('SELECT md5 FROM sticker_library WHERE account=? AND enabled=0',(s.account_id,))}
        excluded={r[0] for r in source.execute('SELECT md5 FROM sticker_chat_library WHERE account=? AND chat=? AND enabled=0',(s.account_id,TARGET))}
        excluded.update(Store.used_stickers(SimpleNamespace(db=source),s.account_id,TARGET,s.sticker_cooldown_seconds))
        runtime.stickers=catalog;runtime.sticker_library.catalog=catalog;runtime.sticker_library.excluded=lambda account,chat:excluded
        guard=object.__new__(SafetyGuard);guard.store=SimpleNamespace(db=source)
        runtime.safety.check=guard.check;runtime.safety.held=guard.held
        content=('鲸鱼娘，性能验收：轻松说一句你准备好聊天啦，再用一张合适的原生表情回应' if mode=='baseline' else
                 '鲸鱼娘，性能验收：轻松说一句你又被逮到摸鱼啦，再用一张合适的原生表情回应')
        first={'key':'maintenance:'+uuid.uuid4().hex,'account':s.account_id,'chat':TARGET,'sender':'维护验收场景',
            'sender_id':'maintenance-test','kind':1,'local_id':0,'is_self':0,'timestamp':time.time(),
            'trigger':'direct','content':content,'payload_json':json.dumps({'maintenance_test':True,'isMentioned':True})}
        store.insert(first);store.observe([first]);await runtime.process_next()
        row=store.get(first['key']);actions=store.actions(first['key'])
        report.update(state=row['state'],error=row['error'],messages_confirmed=sum(a['state']=='sent' for a in actions),
            actions=[{'kind':a['kind'],'state':a['state'],'receipt':a['receipt'],'error':a['error']} for a in actions],
            timing=runtime.last_timing,live_enabled_preserved=api('/api/config',live)['enabled']==s.enabled)
    except Exception as exc:report['error']=str(exc)[:300];raise
    finally:
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        (ASSETS/(mode+'-latest.json')).write_text(json.dumps({**report,'report_path':str(out/'report.json')},ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k!='timing'},ensure_ascii=False),flush=True)
        source.close();await runtime.close()


if __name__=='__main__':asyncio.run(main())
