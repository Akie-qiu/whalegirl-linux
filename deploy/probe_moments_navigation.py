"""Select a failed reply through the gateway GUI guard without sending it."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
import uuid

DATA = Path('/var/lib/whalegirl-linux/core')
ASSETS = Path('/opt/whalegirl-linux-assets')
NATIVE = Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools/moments_ui.py')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--failed-id', required=True)
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    if args.install:
        content = (ASSETS/'moments-navigation-stage.py').read_bytes()
        compile(content, str(NATIVE), 'exec')
        previous = NATIVE.stat()
        temporary = NATIVE.with_name(NATIVE.name+'.probe-'+str(time.time_ns()))
        temporary.write_bytes(content)
        os.chown(temporary, previous.st_uid, previous.st_gid)
        temporary.chmod(previous.st_mode & 0o777)
        os.replace(temporary, NATIVE)
    pid = subprocess.check_output(['systemctl','show','whalegirl-linux','-p','MainPID','--value'], text=True).strip()
    env = dict(value.split(b'=', 1) for value in Path('/proc',pid,'environ').read_bytes().split(b'\0') if b'=' in value)
    jobs = Path(env[b'WHALEGIRL_UI_JOBS_DIR'].decode())
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro', uri=True) as db:
        row = db.execute("SELECT account,payload FROM moments_actions WHERE id=? AND state='failed'", (args.failed_id,)).fetchone()
    if not row:
        raise RuntimeError('Failure state changed')
    account, payload = row
    nonce = uuid.uuid4().hex
    path = jobs/(nonce+'.json')
    path.write_text(json.dumps({'operation':'moments_reply_probe','created':time.time(),'source':json.loads(payload)}))
    path.chmod(0o600)
    request = urllib.request.Request('http://127.0.0.1:6175/api/chats/'+
        urllib.parse.quote(account+'::whalejob::'+nonce, safe='')+'/open?clearUnreads=false',
        method='POST', headers={'Authorization':'Bearer '+(DATA/'gateway.token').read_text().strip()})
    try:
        response = json.load(urllib.request.urlopen(request, timeout=40))
        print(json.dumps({'ok':response.get('ok')}), flush=True)
    finally:
        result = json.loads(path.read_text())
        print(json.dumps({key:value for key,value in result.items() if key in ('state','error','diagnostic')}, ensure_ascii=False), flush=True)
        print(json.dumps({'capture':str(jobs/'moments-reply-navigation.png')}), flush=True)


if __name__ == '__main__':
    main()
