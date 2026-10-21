"""Publish the existing loopback console through a permanent HTTPS reverse proxy."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request

HOST = '120-46-186-50.sslip.io'
ROOT = Path('/opt/whalegirl-linux/current')
DATA = Path('/var/lib/whalegirl-linux/core')
ASSETS = Path('/opt/whalegirl-linux-assets/candidate-4.1.1.8')
BACKUP = ASSETS / ('public-console-before-' + str(time.time_ns()))
BACKUP.mkdir(mode=0o700)
CONFIG = Path('/etc/caddy/Caddyfile')
ENV = Path('/etc/systemd/system/whalegirl-linux.service.d/public-host.conf')
SECRET_DIR = Path('/etc/whalegirl-web')
SECRET_PATH = SECRET_DIR / 'entry-password'

def run(args, **kwargs):
    return subprocess.run(args, check=True, timeout=60, **kwargs)

def health():
    with urllib.request.urlopen('http://127.0.0.1:8092/health', timeout=5) as response:
        return json.load(response)

assert health()['status'] == 'ok'
for source, name in [(ROOT/'whalegirl_linux/app.py','app.py'),
                     (ROOT/'whalegirl_linux/console.js','console.js'),
                     (CONFIG,'Caddyfile'),(ENV,'public-host.conf')]:
    if source.exists():shutil.copy2(source,BACKUP/name)
new_app = (ASSETS/'public-console-app.py').read_bytes()
new_js = (ASSETS/'public-console.js').read_bytes()
compile(new_app,'app.py','exec')
if b"path !== 'auth/login'" not in new_js:raise RuntimeError('Console login authorization update missing')

SECRET_DIR.mkdir(mode=0o700, exist_ok=True)
if SECRET_PATH.exists():
    password = SECRET_PATH.read_text().strip()
else:
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
    password = ''.join(secrets.choice(alphabet) for _ in range(24))
    SECRET_PATH.write_text(password)
    SECRET_PATH.chmod(0o600)
hashed = run(['caddy','hash-password'],input=password+'\n',capture_output=True,text=True).stdout.strip()
if not hashed.startswith('$2'):raise RuntimeError('Caddy bcrypt hash failed')

caddyfile = '''{
    auto_https disable_redirects
}

https://''' + HOST + ''' {
    tls {
        issuer acme {
            disable_http_challenge
        }
    }
    @entry {
        path / /console.js /console.css /api/auth/login*
    }
    basicauth @entry {
        whaleadmin ''' + hashed + '''
    }
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Frame-Options "DENY"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "no-referrer"
    }
    reverse_proxy 127.0.0.1:8092
}
'''
CONFIG.write_text(caddyfile)
validation = subprocess.run(['caddy','validate','--config',str(CONFIG),'--adapter','caddyfile'],
                            capture_output=True,text=True,timeout=20)
if validation.returncode:
    if (BACKUP/'Caddyfile').exists():shutil.copy2(BACKUP/'Caddyfile',CONFIG)
    raise RuntimeError('Caddyfile invalid: '+validation.stderr[-1200:])

ENV.parent.mkdir(parents=True,exist_ok=True)
ENV.write_text('[Service]\nEnvironment=WHALEGIRL_PUBLIC_HOST='+HOST+'\n')
run(['systemctl','daemon-reload'])

try:
    (ROOT/'whalegirl_linux/app.py').write_bytes(new_app)
    (ROOT/'whalegirl_linux/console.js').write_bytes(new_js)
    run(['systemctl','restart','whalegirl-linux.service'])
    for _ in range(30):
        try:
            if health()['status']=='ok':break
        except OSError:pass
        time.sleep(.5)
    else:raise RuntimeError('Core did not become healthy')
    run(['systemctl','enable','--now','caddy.service'])
    result = {'host':HOST,'backup':str(BACKUP),'caddy':'active',
              'core':'active','loopback_bind':True}
    (ASSETS/'public-console-deployment.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result),flush=True)
except Exception:
    run(['systemctl','stop','caddy.service'])
    shutil.copy2(BACKUP/'app.py',ROOT/'whalegirl_linux/app.py')
    shutil.copy2(BACKUP/'console.js',ROOT/'whalegirl_linux/console.js')
    if (BACKUP/'Caddyfile').exists():shutil.copy2(BACKUP/'Caddyfile',CONFIG)
    if (BACKUP/'public-host.conf').exists():shutil.copy2(BACKUP/'public-host.conf',ENV)
    run(['systemctl','daemon-reload'])
    run(['systemctl','restart','whalegirl-linux.service'])
    raise
