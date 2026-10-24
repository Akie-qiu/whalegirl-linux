"""Install bounded reads and receipt recovery without restarting native WeChat."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import time
import urllib.request

BASE = Path('/opt/whalegirl-linux')
ASSETS = Path('/opt/whalegirl-linux-assets')
DATA = Path('/var/lib/whalegirl-linux/core')
TOOLS = Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
NAME = 'whalegirl-wechat-candidate-4118'
EXPECTED = {
    'whalegirl_linux/runtime.py': '9d01bd3579a2b686c3e0f300f49bccebdcd8ed8214c40b57333c18c8dc909308',
    'whalegirl_linux/store.py': '8f01284153e62b6674ba0fda9340b2e16d9c074cdf66284bfd7b49d72f34cd12',
    'whalegirl_linux/gateway.py': '62342495490cba2f5b426ae9a161984724d3a7e14cc95e2ab01f2cc76a16e257',
    'whalegirl_linux/app.py': '71c012a37ad8ee56f6b188e0a64f2bcb6d12f6ce3bce44cbb31098d3edfeae5d',
    'whalegirl_linux/console.js': '219c10a62b84ab6ed20f481cd12b9dcd59c8020ff73e954736711c423a20e605',
    'whalegirl_linux/web.html': 'e605d96196da51569c04729a229b0147a4aeaa1c1e0f5d823695bc475f8d6c4b',
    'deploy/native_message_reader.py': 'eba07050b32f2cb811837129645f8608eb2d5d01cbe6ee3afa52f3f22d6a563a',
    'deploy/start_native_reader.py': 'e440d575ede9f1b1f0330335843c8a44774f56f0041f4fc94cf80a0b124343eb',
    'deploy/collect_stability.py': 'cf976083e48a3191ca5c839d45a840ccd58d5c07fa36e3a64fdc1d53fcb22bac',
    'deploy/a11y_safe.py': 'a174740fe4f84e409aa7815c7427f6fdc7eda3fa64aeac5f34ee1c119fdc4b03',
}


def run(*args, timeout=25):
    return subprocess.check_output(args, text=True, timeout=timeout).strip()


def main():
    current = (BASE/'current').resolve()
    targets = {name: current/name for name in EXPECTED if name.startswith('whalegirl_linux/')}
    targets.update({name: TOOLS/Path(name).name for name in
                    ('deploy/native_message_reader.py', 'deploy/a11y_safe.py')})
    targets.update({'deploy/start_native_reader.py': BASE/'start_native_reader.py',
                    'deploy/collect_stability.py': BASE/'collect_stability.py',
                    'deploy/health_restart_guard.py': TOOLS/'health_restart_guard.py'})
    for name, expected in EXPECTED.items():
        if hashlib.sha256(targets[name].read_bytes()).hexdigest() != expected:
            raise RuntimeError('Source changed; preserved: ' + name)
    with tarfile.open(ASSETS/'message-recovery-20261023.tar') as archive:
        members = archive.getmembers()
        if {m.name for m in members} != set(targets) or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected package contents')
        contents = {m.name: archive.extractfile(m).read() for m in members}
    for name, data in contents.items():
        if name.endswith('.py'):
            compile(data, name, 'exec')

    token = (DATA/'admin.token').read_text().strip()
    def api(route):
        req = urllib.request.Request('http://127.0.0.1:8092/api/'+route,
                                     headers={'Authorization':'Bearer '+token})
        with urllib.request.urlopen(req, timeout=8) as response:
            return json.load(response)
    for _ in range(40):
        state = api('state')
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
            sending = db.execute("SELECT count(*) FROM outbound_actions WHERE state='sending'").fetchone()[0]
            sending += db.execute("SELECT count(*) FROM moments_actions WHERE state='sending'").fetchone()[0]
        if not state.get('processing') and not sending:
            break
        time.sleep(.5)
    else:
        raise RuntimeError('Send still in progress; no services stopped')
    native = run('docker','exec',NAME,'pgrep','-x','wechat')
    info = json.loads(run('docker','inspect',NAME))[0]
    if not info['State']['Running']:
        raise RuntimeError('Pinned container not running')
    stamp = time.strftime('%Y%m%d-%H%M%S')
    backup = ASSETS/('message-recovery-before-'+stamp)
    backup.mkdir(mode=0o700)
    originals = {}
    for name, target in targets.items():
        if target.exists():
            originals[name] = target.read_bytes()
            saved = backup/name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target,saved)
    before_config = hashlib.sha256((DATA/'settings.json').read_bytes()).hexdigest()
    print(json.dumps({'phase':'installing','backup':str(backup)}),flush=True)

    def install(target, data, *, mode=None):
        pending = target.with_name(target.name+'.message-recovery.pending')
        pending.write_bytes(data)
        if target.exists():
            old = target.stat()
            os.chown(pending,old.st_uid,old.st_gid)
            pending.chmod(mode or (old.st_mode&0o777))
        else:
            pending.chmod(mode or 0o644)
        pending.replace(target)

    run('systemctl','stop','whalegirl-linux')
    try:
        run('systemctl','stop','whalegirl-native-reader')
        for name, target in targets.items():
            if (BASE/'current').resolve() != current:
                raise RuntimeError('Current release changed during install')
            install(target,contents[name],mode=0o755 if name.endswith('health_restart_guard.py') else None)
        directory = TOOLS/'health-guard-bin'
        directory.mkdir(mode=0o755, exist_ok=True)
        if {p.name for p in directory.iterdir()}-{'kill'}:
            raise RuntimeError('Guard directory contains other files; preserved')
        install(directory/'kill',contents['deploy/health_restart_guard.py'],mode=0o755)
        run('systemctl','start','whalegirl-native-reader')
        run('systemctl','start','whalegirl-linux')
        for _ in range(45):
            try:
                state = api('state')
                if state.get('gateway',{}).get('status') == 'logged_in':
                    break
            except (OSError,ValueError):
                pass
            time.sleep(.4)
        else:
            raise RuntimeError('Core not ready after install')
        if run('docker','exec',NAME,'pgrep','-x','wechat') != native:
            raise RuntimeError('WeChat process changed during deployment')
        command = "from pathlib import Path; import shutil; p=shutil.which('kill'); assert p=='/usr/local/sbin/kill'; assert Path(p).read_bytes()==Path('/data/native-tools/health_restart_guard.py').read_bytes(); print('installed')"
        if run('docker','exec',NAME,'python3','-c',command) != 'installed':
            raise RuntimeError('Guard not selected by PATH')
        receipts = api('recent?include_ignored=true')
        run('python3',str(BASE/'collect_stability.py'))
        summary = {'at':time.time(),'backup':str(backup),'release':str(current),
                   'native_pid_preserved':True,'wechat':'logged_in','records_returned':len(receipts),
                   'configuration_preserved':before_config==hashlib.sha256((DATA/'settings.json').read_bytes()).hexdigest(),
                   'sha256':{name:hashlib.sha256(target.read_bytes()).hexdigest() for name,target in targets.items()}}
        (ASSETS/'message-recovery-deployment.json').write_text(json.dumps(summary,indent=2))
        print(json.dumps(summary),flush=True)
    except Exception:
        run('systemctl','stop','whalegirl-linux')
        run('systemctl','stop','whalegirl-native-reader')
        for name, data in originals.items():
            install(targets[name],data)
        run('systemctl','start','whalegirl-native-reader')
        run('systemctl','start','whalegirl-linux')
        raise


if __name__ == '__main__':
    main()
