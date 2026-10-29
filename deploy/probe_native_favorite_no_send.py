"""Locate the failed favorite while blocking the final send click."""
import base64
import json
from pathlib import Path
import subprocess
import sys

CONTAINER='whalegirl-wechat-candidate-4118'
JOBS=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/whalegirl-jobs')
failed=[]
for path in JOBS.glob('*.json'):
    try:value=json.loads(path.read_text())
    except (ValueError,OSError):continue
    if value.get('operation')=='send_sticker' and 'outside the currently exposed native page' in value.get('error',''):
        failed.append(value)
if not failed:raise RuntimeError('No matching favorite failure to inspect')
md5=max(failed,key=lambda value:value.get('created',0))['md5']
source=base64.b64encode(Path(sys.argv[1]).read_bytes()).decode()
script='''import os,json,time,base64,subprocess
from pathlib import Path
pid=subprocess.check_output(['pgrep','-xo','wechat'],text=True).strip()
env=dict(v.split('=',1) for v in Path('/proc/'+pid+'/environ').read_bytes().decode().split(chr(0)) if '=' in v)
os.environ['DISPLAY']=':99'
os.environ['DBUS_SESSION_BUS_ADDRESS']=env['DBUS_SESSION_BUS_ADDRESS']
module={'__name__':'favorite_probe'}
exec(compile(base64.b64decode(SOURCE),'/opt/tools/chat-select.py','exec'),module)
module['SKIP_MESSAGES']=True
job={'md5':MD5,'created':time.time()}
class ProbePath:
    parent=Path('/tmp/favorite-probe-'+str(time.time_ns()))
    def write_text(self,*args,**kwargs):pass
path=ProbePath();path.parent.mkdir(mode=0o700)
original=module['run_process']
class SendBlocked(Exception):pass
def guarded(args,*a,**kw):
    if args[0]=='/opt/tools/click' and job.get('state')=='sending':raise SendBlocked()
    try:return original(args,*a,**kw)
    except subprocess.CalledProcessError as exc:
        if args[0]=='/opt/tools/a11y-dump':
            try:error=json.loads(exc.stdout).get('error','')
            except (TypeError,ValueError):error='unavailable'
            raise RuntimeError('Accessibility snapshot: '+error) from None
        raise
module['run_process']=guarded
module['selected']=lambda *a:True
snapshots=[]
original_tree=module['tree']
def inspect_tree():
    root=original_tree()
    tiles=[n for n in module['walk'](root) if n.get('role')=='label' and n.get('name')=='Custom Stickers' and (n.get('bounds') or {}).get('width',0)>50]
    snapshots.append({'tiles':len(tiles),'top':min((n['bounds']['y'] for n in tiles),default=None),'bottom':max((n['bounds']['y'] for n in tiles),default=None)})
    return root
module['tree']=inspect_tree
start=time.monotonic()
try:
    module['send_native_sticker'](job,path,module['tree'](),'inspection',True)
    raise RuntimeError('Probe did not block a send boundary')
except SendBlocked:
    print(json.dumps({'located':True,'send_blocked':True,'seconds':round(time.monotonic()-start,3),'scroll':job.get('favorite_scroll'),'cache':job.get('favorite_cache')}))
except Exception as exc:
    order=[r['md5'] for r in module['native_read']('/stickers')['stickers']]
    print(json.dumps({'located':False,'send_blocked':True,'error':str(exc),'index':order.index(MD5),'total':len(order),'snapshots':snapshots,'scroll':job.get('favorite_scroll')}))
'''.replace('SOURCE',repr(source)).replace('MD5',repr(md5))
result=subprocess.run(['docker','exec','-i',CONTAINER,'python3','-'],input=script,text=True,capture_output=True,timeout=35)
if result.returncode:
    print(result.stderr[-1800:])
    raise RuntimeError('No-send favorite inspection failed')
report=json.loads(result.stdout.strip())
Path(sys.argv[1]).with_name('native-favorite-probe.json').write_text(json.dumps(report))
print(json.dumps(report))
