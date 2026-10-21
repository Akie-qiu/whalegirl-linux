"""Deploy DeepSeek Search/Fetch integration without restarting native WeChat."""
import asyncio
import json
import os
from pathlib import Path
import pwd
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request

BASE=Path('/opt/whalegirl-linux');DATA=Path('/var/lib/whalegirl-linux/core');ASSETS=Path('/opt/whalegirl-linux-assets')
current=BASE/'current';previous=current.resolve();stamp=time.strftime('%Y%m%d-%H%M%S')
stage=BASE/'releases'/('web-search-'+stamp);backup=ASSETS/('web-search-before-'+stamp);backup.mkdir(mode=0o700)
expected={'whalegirl_linux/'+n for n in ['settings.py','search.py','runtime.py','conversation.py','web.html','habits.js']}|{'tests/test_search.py'}
with tarfile.open(ASSETS/'web-search-20261021.tar') as archive:
    members=archive.getmembers()
    if {m.name for m in members}!=expected or any(not m.isfile() for m in members):raise RuntimeError('Unexpected archive contents')
    payload={m.name:archive.extractfile(m).read() for m in members}
for name,data in payload.items():
    if name.endswith('.py'):compile(data,name,'exec')
shutil.copytree(previous,stage,symlinks=True)
for name,data in payload.items():
    p=stage/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
tests=subprocess.run([str(BASE/'venv/bin/python'),'-m','unittest',
    'test_search.SearchApiTests','test_search.SearchRuntimeTests.test_search_tool_runs_between_fast_model_turns_without_quotes','-v'],
    cwd=stage/'tests',env={**os.environ,'PYTHONPATH':str(stage)},capture_output=True,text=True,timeout=30)
(backup/'focused-tests.log').write_text(tests.stdout+tests.stderr)
print((tests.stdout+tests.stderr)[-3200:],flush=True)
if tests.returncode:raise RuntimeError('Search integration checks failed; live core not changed')
sys.path.insert(0,str(stage))
from whalegirl_linux.settings import Config,Settings
from whalegirl_linux.store import Store
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.gateway import Receipt

async def live_probe():
    config=Config(DATA)
    config.settings=config.settings.model_copy(update={'enabled':True,'web_search_enabled':True,'web_search_result_count':3,
        'debounce_single_seconds':0,'debounce_burst_seconds':0,'debounce_max_seconds':0})
    probe_dir=backup/'real-model-probe';probe_dir.mkdir(mode=0o700)
    store=Store(probe_dir/'state.sqlite3')
    class Capture:
        login={}
        def __init__(self):self.messages=[]
        async def status(self):return {'status':'logged_in','loggedInUser':config.settings.account_id}
        async def send(self,chat,text,**kwargs):self.messages.append(text);return Receipt(True,'probe-'+str(len(self.messages)))
        async def send_sticker(self,chat,md5):raise RuntimeError('Probe should answer in text')
        async def close(self):pass
    capture=Capture();runtime=Runtime(config,store,capture,stage/'styles')
    try:
        event={'key':'public-web-search-probe','account':config.settings.account_id,'chat':config.settings.allowed_chat_ids[0],
            'sender':'公开问题验收样例','sender_id':'admin-probe','kind':1,'is_self':0,'local_id':1,
            'timestamp':time.time(),'trigger':'direct',
            'content':'鲸鱼娘，联网搜索一下 DeepSeek 官方的网页搜索接口地址和它的返回字段，简单说两句就好，不用引用'}
        store.insert(event);store.observe([event]);await runtime.process_next()
        result={'actual_wechat_messages_sent':0,'captured_reply':capture.messages,'timing':runtime.last_timing,'error':runtime.last_error}
        (probe_dir/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        if not runtime.last_timing.get('web_search',{}).get('ok') or not capture.messages:
            raise RuntimeError('Real model did not complete search and normal-chat reply')
        if any('%cite' in text or '%/cite%' in text for text in capture.messages):raise RuntimeError('Search citation token escaped into output')
        fetched=await runtime.web_search.fetch(await runtime.model(),'https://api-docs.deepseek.com/zh-cn/guides/search',config.settings)
        if not fetched['ok']:raise RuntimeError('Official Fetch API check failed: '+fetched['error'])
        print(json.dumps({'real_search_pipeline':result,'fetch_api':{k:v for k,v in fetched.items() if k!='text'}},ensure_ascii=False),flush=True)
        return result
    finally:await runtime.close()
live=asyncio.run(live_probe())
user=pwd.getpwnam('whalegirl-ai')
for p in [stage,*stage.rglob('*')]:
    if not p.is_symlink():os.chown(p,0,user.pw_gid);p.chmod(0o750 if p.is_dir() else 0o640)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));token=(DATA/'admin.token').read_text().strip()
def api(route,body=None):
    req=urllib.request.Request('http://127.0.0.1:8092/api/'+route,data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with opener.open(req,timeout=10) as response:return json.load(response)
original=api('config');original.pop('api_key_configured',None);state=api('state')
if state.get('paused_reason') or state.get('counts',{}).get('uncertain'):raise RuntimeError('Core has a send awaiting review')
(backup/'settings.json').write_text(json.dumps(original,ensure_ascii=False,indent=2))
native_before=subprocess.check_output(['docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat'],text=True).strip()
print('Enabling search in Python core; native WeChat login is preserved',flush=True)
api('config',{'settings':{**original,'enabled':False}});stopped=False
try:
    for _ in range(200):
        if not api('state').get('processing'):break
        time.sleep(.3)
    else:raise RuntimeError('Active core operation did not drain')
    if api('state').get('counts',{}).get('uncertain'):raise RuntimeError('Unconfirmed send; upgrade postponed')
    subprocess.run(['systemctl','stop','whalegirl-linux'],check=True,timeout=20);stopped=True
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        with sqlite3.connect(backup/'state.sqlite3') as dest:db.backup(dest)
    pending=BASE/('current.web-search-'+stamp);pending.symlink_to(stage,target_is_directory=True);os.replace(pending,current)
    settings={**original,'web_search_enabled':True,'web_search_result_count':original.get('web_search_result_count',5),
              'web_search_timeout':original.get('web_search_timeout',35)}
    p=DATA/'settings.json';p.write_text(json.dumps(settings,ensure_ascii=False,indent=2));os.chown(p,user.pw_uid,user.pw_gid);p.chmod(0o600)
    subprocess.run(['systemctl','start','whalegirl-linux'],check=True,timeout=20)
    for _ in range(50):
        try:
            state=api('state');saved=api('config')
            if state['gateway']['status']=='logged_in' and saved.get('web_search_enabled'):break
        except (OSError,ValueError):pass
        time.sleep(.3)
    else:raise RuntimeError('New core is not ready')
    native_after=subprocess.check_output(['docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat'],text=True).strip()
    report={'release':str(stage),'backup':str(backup),'focused_checks':'4 passed','real_search_and_fetch':'passed',
        'wechat_status':state['gateway']['status'],'enabled':state['enabled'],'paused_reason':state['paused_reason'],
        'web_search_enabled':saved['web_search_enabled'],'native_wechat_process_unchanged':native_before==native_after,
        'chat_policies_preserved':saved['allowed_chat_ids']==original['allowed_chat_ids'] and saved['trigger_only_chat_ids']==original['trigger_only_chat_ids']}
    (ASSETS/'web-search-deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False),flush=True)
except BaseException:
    if stopped:
        subprocess.run(['systemctl','stop','whalegirl-linux'],check=True,timeout=20)
        link=BASE/('current.web-search-rollback-'+stamp);link.symlink_to(previous,target_is_directory=True);os.replace(link,current)
    p=DATA/'settings.json';p.write_text(json.dumps(original,ensure_ascii=False,indent=2));os.chown(p,user.pw_uid,user.pw_gid);p.chmod(0o600)
    subprocess.run(['systemctl','start','whalegirl-linux'],check=True,timeout=20)
    raise
