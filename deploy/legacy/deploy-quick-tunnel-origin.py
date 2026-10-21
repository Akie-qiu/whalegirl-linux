"""Prepare a loopback-only, password-gated origin for Cloudflare Quick Tunnel."""

import os
CONSOLE_PASSWORD = os.environ["WHALEGIRL_CONSOLE_PASSWORD"]
import base64
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

ROOT=Path('/opt/whalegirl-linux/current')
DATA=Path('/var/lib/whalegirl-linux/core')
ASSETS=Path('/opt/whalegirl-linux-assets/candidate-4.1.1.8')
CADDY=Path('/etc/caddy/Caddyfile')
PASSWORD=Path('/etc/whalegirl-web/entry-password').read_text().strip()
BACKUP=ASSETS/('quick-tunnel-before-'+str(time.time_ns()))
BACKUP.mkdir(mode=0o700)
shutil.copy2(ROOT/'whalegirl_linux/app.py',BACKUP/'app.py')
shutil.copy2(CADDY,BACKUP/'Caddyfile')
source=(ASSETS/'public-console-app.py').read_bytes()
compile(source,'app.py','exec')
if b"host.endswith('.trycloudflare.com')" not in source:
    raise RuntimeError('Tunnel host validation is missing')
current=CADDY.read_text()
match=re.search(r'whaleadmin\s+(\$2[aby]\$\S+)',current)
if not match:raise RuntimeError('Existing strong web credential was not found')
config='''http://:8093 {
    bind 127.0.0.1
    @entry {
        path / /console.js /console.css /api/auth/login*
    }
    basicauth @entry {
        whaleadmin '''+match.group(1)+'''
    }
    reverse_proxy 127.0.0.1:8092
}
'''
CADDY.write_text(config)
result=subprocess.run(['caddy','validate','--config',str(CADDY),'--adapter','caddyfile'],capture_output=True,text=True,timeout=15)
if result.returncode:
    shutil.copy2(BACKUP/'Caddyfile',CADDY)
    raise RuntimeError('Origin config invalid: '+result.stderr[-500:])

token=(DATA/'admin.token').read_text().strip()
def state():
    request=urllib.request.Request('http://127.0.0.1:8092/api/state',headers={'Authorization':'Bearer '+token})
    return json.load(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=10))
for _ in range(60):
    value=state()
    if not value['processing'] and not any(value['counts'].get(x) for x in ('sending','generating')):break
    time.sleep(.5)
else:raise RuntimeError('Core still sending; defer restart')

try:
    (ROOT/'whalegirl_linux/app.py').write_bytes(source)
    subprocess.run(['systemctl','restart','whalegirl-linux.service'],check=True,timeout=30)
    for _ in range(30):
        try:state();break
        except OSError:time.sleep(.5)
    else:raise RuntimeError('Core failed to become healthy')
    subprocess.run(['systemctl','restart','caddy.service'],check=True,timeout=20)
    auth='Basic '+base64.b64encode(('whaleadmin:'+PASSWORD).encode()).decode()
    base='http://127.0.0.1:8093'
    request=urllib.request.Request(base+'/',headers={'Host':'test.trycloudflare.com'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:opener.open(request,timeout=6)
    except urllib.error.HTTPError as error:assert error.code==401
    else:raise RuntimeError('Anonymous origin access not challenged')
    request=urllib.request.Request(base+'/',headers={'Host':'test.trycloudflare.com','Authorization':auth})
    with opener.open(request,timeout=6) as response:
        assert response.status==200 and b'console.js' in response.read()
    request=urllib.request.Request(base+'/api/auth/login',data=json.dumps({"password":CONSOLE_PASSWORD}).encode(),
        headers={'Host':'test.trycloudflare.com','Origin':'https://test.trycloudflare.com',
                 'Authorization':auth,'Content-Type':'application/json'})
    with opener.open(request,timeout=6) as response:
        assert json.load(response)['token']
    print(json.dumps({'origin':'127.0.0.1:8093','anonymous_http_status':401,
                      'login_test':'passed','backup':str(BACKUP)},ensure_ascii=False))
except Exception:
    shutil.copy2(BACKUP/'app.py',ROOT/'whalegirl_linux/app.py')
    shutil.copy2(BACKUP/'Caddyfile',CADDY)
    subprocess.run(['systemctl','restart','whalegirl-linux.service'],timeout=30)
    subprocess.run(['systemctl','restart','caddy.service'],timeout=20)
    raise
