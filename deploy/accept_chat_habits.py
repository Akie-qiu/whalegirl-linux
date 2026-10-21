"""One authorized live send-plan check in 临时测试, with persistent receipts and no automatic replay."""
import asyncio
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request

sys.path.insert(0,'/opt/whalegirl-linux/current')
from whalegirl_linux.gateway import AgentGateway
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store

DATA=Path('/var/lib/whalegirl-linux/core')
REPORT=Path('/opt/whalegirl-linux-assets/chat-habits-live-acceptance.json')
token=(DATA/'admin.token').read_text().strip()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

def api(route,body=None):
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with opener.open(request,timeout=10) as response:return json.load(response)


class ExistingStore(Store):
    def __init__(self,path):
        # Do not run normal startup recovery on a database owned by a running core.
        self.db=sqlite3.connect(path,check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA busy_timeout=5000')
        self.lock=threading.RLock()


async def main():
    if REPORT.exists():
        print(REPORT.read_text());return
    original=api('config');original.pop('api_key_configured',None)
    chats=api('gateway/chats')
    selected=[c for c in chats if c.get('name')=='临时测试' and c.get('id') in original['allowed_chat_ids']]
    if len(selected)!=1:raise RuntimeError('Authorized acceptance chat was not uniquely resolved')
    chat=selected[0]['id'];state=api('state')
    if state.get('paused_reason') or state.get('counts',{}).get('uncertain') or state['gateway']['status']!='logged_in':
        raise RuntimeError('Live acceptance requires an online, unpaused core')
    api('config',{'settings':{**original,'enabled':False}})
    runtime=None;store=None;event=None
    try:
        for _ in range(180):
            if not api('state').get('processing'):break
            await asyncio.sleep(.3)
        else:raise RuntimeError('Core operation did not drain')
        # Reuse the installed gateway configuration without printing environment values.
        environment=subprocess.check_output(['systemctl','show','whalegirl-linux','-p','Environment','--value'],text=True)
        for entry in shlex.split(environment):
            name,sep,value=entry.partition('=')
            if sep and name.startswith('WHALEGIRL_'):os.environ[name]=value
        config=Config(DATA);config.settings=Settings.model_validate(original)
        store=ExistingStore(DATA/'state.sqlite3')
        key=config.settings.account_id+'/'+chat+'/admin-accept-chat-habits-20261021'
        if store.get(key):raise RuntimeError('Acceptance already attempted; inspect existing receipt instead of replaying')
        gateway=AgentGateway(os.environ.get('WHALEGIRL_GATEWAY_URL','http://127.0.0.1:6175'),config.gateway_token,control_directory=DATA)
        runtime=Runtime(config,store,gateway,Path('/opt/whalegirl-linux/current/styles'))
        latest=store.db.execute("SELECT max(sent_at) FROM outbound_actions WHERE account=? AND state IN ('sent','uncertain')",(config.settings.account_id,)).fetchone()[0]
        runtime.last_send=latest or 0
        catalog=api('stickers')['stickers'];md5='9f1dab92333da8587c6e74d9d37e2e97'
        if md5 not in catalog:raise RuntimeError('The selected native favorite is not currently available')
        event={'key':key,'account':config.settings.account_id,'chat':chat,'sender':'管理端验收',
               'sender_id':'admin-acceptance','local_id':0,'trigger':'direct','timestamp':time.time(),
               'content':'管理员授权的真实混合发送验收（不是群友入站消息）'}
        store.insert(event,'generating')
        actions=[{'type':'text','text':'升级验收：这条是文字，下一条是微信原生表情','display':'升级验收：这条是文字，下一条是微信原生表情'},
                 {'type':'sticker','id':md5,'display':'[原生表情] '+catalog[md5]},
                 {'type':'text','text':'表情后的补一句，混合发送验收完成','display':'表情后的补一句，混合发送验收完成'}]
        print('Checking one native text/sticker/text plan in the authorized test group',flush=True)
        await runtime.send_plan(event,[event],event,actions,{},config.generation,{'acceptance':'admin_live_send_plan'})
        row=store.get(key)
        report={'chat_name':'临时测试','actual_messages_confirmed':len([a for a in store.actions(key) if a['state']=='sent']),
                'state':row['state'],'actions':runtime.last_timing.get('actions'),
                'total_seconds':runtime.last_timing.get('total_seconds'),'error':row.get('error'),
                'native_wechat_restarted':False,'automatic_replay':False,'created_at':time.time()}
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False),flush=True)
        if row['state']=='uncertain':runtime.safety.hold(config.settings.account_id,'acceptance_uncertain','原生混合发送验收结果待人工核对')
    except BaseException:
        if runtime and event:
            current=store.get(event['key'])
            if current and current['state']=='sending':
                store.update(event['key'],'uncertain',reply=current['reply'],error='管理端验收被中断，请核对原生发送结果')
                runtime.safety.hold(config.settings.account_id,'acceptance_uncertain','管理端验收发送中断，需人工核对')
        raise
    finally:
        if runtime:await runtime.close()
        elif store:store.close()
        # Preserve any settings edited by the user during the check.
        current=api('config');current.pop('api_key_configured',None)
        api('config',{'settings':{**current,'enabled':original['enabled']}})

asyncio.run(main())
