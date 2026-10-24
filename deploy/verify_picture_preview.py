"""Inspect the image preview in the authorized test chat; never press Send."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.parse
import urllib.request
import uuid

data=Path('/var/lib/whalegirl-linux/core')
token=(data/'admin.token').read_text().strip()
def api(route):
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+route,headers={'Authorization':'Bearer '+token})
    return json.load(urllib.request.urlopen(request,timeout=15))

rows=api('gateway/chats')
rows=rows.get('chats',[]) if isinstance(rows,dict) else rows
chat=next(c for c in rows if (c.get('remark') or c.get('name'))=='临时测试')
asset=next(a for a in api('images')['assets'] if a['source']=='generated')
pid=subprocess.check_output(['systemctl','show','whalegirl-linux','-p','MainPID','--value'],text=True).strip()
env=dict(x.split('=',1) for x in Path('/proc/'+pid+'/environ').read_text().split('\0') if '=' in x)
nonce=uuid.uuid4().hex
path=Path(env['WHALEGIRL_UI_JOBS_DIR'])/(nonce+'.json')
path.write_text(json.dumps({'operation':'open_chat','chat':chat['id'],'created':time.time()}));path.chmod(0o600)
owner=(path.parent/'authorized-chats.json').stat();os.chown(path,owner.st_uid,owner.st_gid)
url=env['WHALEGIRL_GATEWAY_URL'].rstrip('/')+'/api/chats/'+urllib.parse.quote(chat['id']+'::whalejob::'+nonce,safe='')+'/open?clearUnreads=false'
request=urllib.request.Request(url,data=b'',headers={'Authorization':'Bearer '+(data/'gateway.token').read_text().strip()})
result=json.load(urllib.request.urlopen(request,timeout=40))
if not result.get('ok'):
    selector="""import pathlib,os,sys,json
env=dict(x.split('=',1) for x in pathlib.Path('/proc/27/environ').read_text().split('\\0') if '=' in x)
os.environ.update({k:env[k] for k in ['DISPLAY','DBUS_SESSION_BUS_ADDRESS'] if k in env})
sys.path.insert(0,'/data/native-tools')
import chat_select_linux as c
sys.argv=['chat_select_linux.py',sys.argv[1]]
print(json.dumps(c.main()))
"""
    result=json.loads(subprocess.check_output(['docker','exec','whalegirl-wechat-candidate-4118','python3','-c',selector,chat['id']+'::whalejob::'+nonce],text=True,timeout=20))
    if not result.get('ok'):raise RuntimeError('Could not open authorized test chat')
script='''import sys,json,time,subprocess,pathlib,os
env=dict(x.split('=',1) for x in pathlib.Path('/proc/27/environ').read_text().split('\\0') if '=' in x)
os.environ.update({k:env[k] for k in ['DISPLAY','DBUS_SESSION_BUS_ADDRESS'] if k in env})
sys.path.insert(0,'/data/native-tools')
import chat_select_linux as c
c.SKIP_MESSAGES=True
root=c.tree()
editors=[n for n in c.walk(root) if n.get('role')=='text' and 'EDITABLE' in (n.get('states') or []) and (n.get('bounds') or {}).get('height',0)>35 and (n.get('bounds') or {}).get('y',0)>200]
if len(editors)!=1:raise RuntimeError('No unique editor')
c.click_node(editors[0])
try:
 c.run_process(['xclip','-selection','clipboard','-t','image/png','-i',sys.argv[1]],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3)
 c.run_process(['xdotool','key','--clearmodifiers','ctrl+v'],check=True,timeout=3)
 time.sleep(.7);tree=c.tree()
 print(json.dumps({'preview_controls':[{'role':n.get('role'),'name':n.get('name'),'states':n.get('states')} for n in c.walk(tree) if n.get('role') in ['frame','dialog','push-button']][-35:]},ensure_ascii=False))
finally:c.run_process(['xdotool','key','--clearmodifiers','Escape'],check=True,timeout=3)
'''
result=subprocess.check_output(['docker','exec','whalegirl-wechat-candidate-4118','python3','-c',script,
    '/data/whalegirl-jobs/moments-assets/'+asset['id']+'.png'],text=True,timeout=20)
print(result)
