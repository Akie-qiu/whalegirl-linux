"""Exercise the real picker while unconditionally blocking every tile-send click."""
import base64
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import urllib.request

BACKUP = Path('/opt/whalegirl-linux-assets/sticker-reliability-20261112')
DATA = Path('/var/lib/whalegirl-linux/core')
CONTAINER = 'whalegirl-wechat-candidate-4118'


def state():
    request = urllib.request.Request('http://127.0.0.1:8092/api/state',
        headers={'Authorization': 'Bearer ' + (DATA / 'admin.token').read_text().strip()})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def run():
    deadline = time.monotonic() + 90
    def busy():
        with sqlite3.connect('file:' + str(DATA/'state.sqlite3') + '?mode=ro', uri=True) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            inflight=sum(db.execute('SELECT count(*) FROM '+name+
                " WHERE state IN ('sending','generating')").fetchone()[0]
                for name in ('outbound_actions','moments_actions','picture_jobs') if name in tables)
        return state().get('processing') or inflight
    while busy():
        if time.monotonic() >= deadline:
            raise TimeoutError('No idle interval available for a non-sending probe')
        time.sleep(1)
    if state().get('gateway', {}).get('status') != 'logged_in':
        raise RuntimeError('WeChat is offline')
    stopped = False
    try:
        subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=True, timeout=30)
        stopped = True
        candidate = '--candidate' in sys.argv[1:]
        selector = (BACKUP/'stage/deploy/chat_select_linux.py').read_bytes() if candidate else None
        script = 'selector_source_b64=' + repr(base64.b64encode(selector).decode() if selector else '') + '\n' + r'''
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

pid=subprocess.check_output(['pgrep','-xo','wechat'],text=True).strip()
env=dict(v.split('=',1) for v in Path('/proc/'+pid+'/environ').read_bytes().decode().split(chr(0)) if '=' in v)
os.environ['DISPLAY']=':99'
os.environ['DBUS_SESSION_BUS_ADDRESS']=env['DBUS_SESSION_BUS_ADDRESS']
spec=importlib.util.spec_from_file_location('sticker_probe_selector','/data/native-tools/chat_select_linux.py')
ui=importlib.util.module_from_spec(spec);spec.loader.exec_module(ui)
if selector_source_b64:
    exec(compile(base64.b64decode(selector_source_b64),ui.__file__,'exec'),ui.__dict__)
ui.SKIP_MESSAGES=True
root=ui.tree()
matched=[r for r in ui.chats() if ui.selected(root,ui.normal(r.get('remark') or r.get('name')),bool(r.get('isGroup')))]
if len(matched)!=1:raise RuntimeError('Current chat is not uniquely verifiable; no probe action')
row=matched[0];name=ui.normal(row.get('remark') or row.get('name'));is_group=bool(row.get('isGroup'))
order=[r['md5'] for r in ui.native_read('/stickers')['stickers']]
if not order:raise RuntimeError('No native favorites to inspect')
folder=Path(tempfile.mkdtemp(prefix='sticker-probe-',dir='/data/whalegirl-jobs'));folder.chmod(0o700)
original=ui.run_process
class SendBlocked(Exception):pass
results=[]
for index in dict.fromkeys([0,len(order)//2,len(order)-1]):
    job={'md5':order[index],'chat':row['id'],'created':time.time()}
    clicks=[]
    retries_before=ui.PROFILE.get('snapshot_contention_retries',0)
    def guarded(args,*a,**kw):
        if args[0]=='/opt/tools/click' and job.get('state')=='sending':
            clicks.append('blocked')
            raise SendBlocked()
        return original(args,*a,**kw)
    ui.run_process=guarded
    started=time.monotonic()
    result={'index':index,'send_click_blocked':False}
    try:
        ui.send_native_sticker(job,folder/('probe-'+str(index)),ui.tree(),name,is_group)
        result['error']='Probe did not stop before the final send click'
    except SendBlocked:
        result['send_click_blocked']=True
    except Exception as exc:
        result['error']=str(exc)[:180]
    result.update(seconds=round(time.monotonic()-started,3),cache=job.get('favorite_cache'),
                  scroll=job.get('favorite_scroll'),final_send_clicks_passed=0,
                  snapshot_contention_retries=ui.PROFILE.get('snapshot_contention_retries',0)-retries_before)
    results.append(result)
ui.run_process=original
print(json.dumps({'favorite_count':len(order),'all_located':all(r['send_click_blocked'] for r in results),
                  'results':results,'wechat_test_messages_sent':0},ensure_ascii=False))
'''
        result = subprocess.run(['docker', 'exec', '-u', '0', '-i', CONTAINER, 'python3', '-'],
            input=script, text=True, capture_output=True, timeout=90)
        if result.returncode:
            raise RuntimeError('Non-sending picker probe failed: ' + result.stderr[-900:])
        report = json.loads(result.stdout.strip())
        report['candidate_source'] = candidate
        report['at'] = time.time()
        path = BACKUP / ('no-send-probe-' + str(time.time_ns()) + '.json')
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        path.chmod(0o600)
        print(json.dumps(report, ensure_ascii=False))
    finally:
        if stopped:
            subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=True, timeout=30)


if __name__ == '__main__':
    run()
