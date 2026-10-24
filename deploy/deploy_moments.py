"""Deploy the SNS feature without restarting native WeChat or changing chat settings."""
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tarfile
import time
import urllib.request

BASE = Path('/opt/whalegirl-linux')
DATA = Path('/var/lib/whalegirl-linux/core')
ASSETS = Path('/opt/whalegirl-linux-assets')
TOOLS = Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
JOBS = TOOLS.parent / 'whalegirl-jobs'
APP = ('app.py','settings.py','runtime.py','gateway.py','moments.py','moments_payload.py','moments.js','console.js','web.html','safety.py')
NATIVE = {'chat_select_linux.py':'deploy/chat_select_linux.py', 'native_message_reader.py':'deploy/native_message_reader.py',
          'moments_ui.py':'deploy/moments_ui.py', 'moments_payload.py':'whalegirl_linux/moments_payload.py'}
FILES = {'whalegirl_linux/'+p for p in APP} | set(NATIVE.values()) | {'tests/test_moments.py','MOMENTS.md','deploy/deploy_moments.py','deploy/start_native_reader.py'}
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
token = (DATA / 'admin.token').read_text().strip()


def api(route, body=None):
    req = urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},
        data=None if body is None else json.dumps(body).encode())
    with opener.open(req, timeout=18) as response:
        return json.load(response)


def command(*args, timeout=40):
    return subprocess.run(list(args), check=True, capture_output=True, text=True, timeout=timeout).stdout.strip()


def idle():
    until = time.monotonic()+45
    while True:
        value = api('state')
        if not value.get('processing'):
            return value
        if time.monotonic() >= until:
            raise RuntimeError('Chat is busy; deployment deferred')
        time.sleep(.25)


def deploy():
    stamp = time.strftime('%Y%m%d-%H%M%S')
    previous = (BASE / 'current').resolve()
    stage = BASE / 'releases' / ('moments-'+stamp)
    backup = ASSETS / ('moments-before-'+stamp)
    backup.mkdir(mode=0o700)
    with tarfile.open(ASSETS/'moments-20261022.tar') as archive:
        members = archive.getmembers()
        if {m.name for m in members} != FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected deployment payload')
        contents = {m.name:archive.extractfile(m).read() for m in members}
    for name, data in contents.items():
        if name.endswith('.py'):
            compile(data, name, 'exec')
    shutil.copytree(previous, stage, symlinks=True)
    for name, data in contents.items():
        target = stage / name
        target.parent.mkdir(exist_ok=True, parents=True)
        target.write_bytes(data)
    for pattern in ('test_moments.py','test_media_delivery.py'):
        result = subprocess.run([str(BASE/'venv/bin/python'),'-m','unittest','discover','-s','tests','-p',pattern,'-v'],
            cwd=stage, capture_output=True, text=True, timeout=40)
        (backup/(pattern+'.log')).write_text(result.stdout+result.stderr)
        print((result.stdout+result.stderr)[-2800:], flush=True)
        if result.returncode:
            raise RuntimeError('Focused checks failed; live deployment unchanged')
    gid = pwd.getpwnam('whalegirl-ai').pw_gid
    for path in (stage, *stage.rglob('*')):
        if not path.is_symlink():
            os.chown(path, 0, gid)
            path.chmod(0o750 if path.is_dir() else 0o640)
    idle()
    original = api('config')
    original.pop('api_key_configured', None)
    shutil.copy2(DATA/'settings.json', backup/'settings.json')
    if (JOBS/'authorized-chats.json').exists():
        shutil.copy2(JOBS/'authorized-chats.json', backup/'authorized-chats.json')
    native_pid = command('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')
    originals = {}
    original_starter = (BASE/'start_native_reader.py').read_bytes()
    (backup/'start_native_reader.py').write_bytes(original_starter)
    original_mounted = subprocess.run(['docker','exec','whalegirl-wechat-candidate-4118','cat','/opt/tools/chat-select.py'],
        capture_output=True, check=True, timeout=8).stdout
    (backup/'mounted-chat-select.py').write_bytes(original_mounted)
    for name in NATIVE:
        target = TOOLS/name
        if target.exists():
            originals[name] = target.read_bytes()
            shutil.copy2(target, backup/name)
    stopped = False
    try:
        api('config', {'settings':{'enabled':False}})
        idle()
        command('systemctl','stop','whalegirl-linux')
        stopped = True
        for name, source in NATIVE.items():
            target = TOOLS/name
            owner = (target if target.exists() else TOOLS/'chat_select_linux.py').stat()
            temporary = target.with_name(target.name+'.moments-'+stamp)
            temporary.write_bytes(contents[source])
            os.chown(temporary, owner.st_uid, owner.st_gid)
            temporary.chmod(owner.st_mode & 0o777)
            os.replace(temporary, target)
        # Atomic host replacements do not update an existing single-file bind.
        # Update that writable bind in place as well, without restarting WeChat.
        subprocess.run(['docker','exec','-i','whalegirl-wechat-candidate-4118','python3','-c',
            "import pathlib,sys;pathlib.Path('/opt/tools/chat-select.py').write_bytes(sys.stdin.buffer.read())"],
            input=contents['deploy/chat_select_linux.py'], check=True, capture_output=True, timeout=8)
        (BASE/'start_native_reader.py').write_bytes(contents['deploy/start_native_reader.py'])
        command('systemctl','restart','whalegirl-native-reader')
        pending = BASE/('current.moments-'+stamp)
        pending.symlink_to(stage, target_is_directory=True)
        os.replace(pending, BASE/'current')
        command('systemctl','start','whalegirl-linux')
        for _ in range(80):
            try:
                if api('state').get('gateway',{}).get('status') in ('logged_in','logged_out'):
                    break
            except (OSError, ValueError):
                pass
            time.sleep(.25)
        else:
            raise RuntimeError('New core did not become ready')
        # A normal config save publishes the new native authorization flags.
        api('config', {'settings':{'enabled':original['enabled']}})
        live = api('config')
        if any(live.get(k) != v for k,v in original.items()):
            raise RuntimeError('Existing settings changed unexpectedly')
        state = api('state')
        pid_after = command('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')
        if pid_after != native_pid:
            raise RuntimeError('Native WeChat changed during deployment')
        result = {'release':str(stage),'previous':str(previous),'backup':str(backup),
                  'native_pid_preserved':True,'wechat':state['gateway']['status'],
                  'enabled':live['enabled'],'moments':{k:v for k,v in live.items() if k.startswith('moments_')},
                  'real_publications':0,'real_comments':0}
        report = ASSETS/'moments-deployment.json'
        report.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        report.chmod(0o600)
        print(json.dumps({k:v for k,v in result.items() if k!='moments'}, ensure_ascii=False))
    except Exception:
        if stopped:
            command('systemctl','stop','whalegirl-linux')
            for name, data in originals.items():
                (TOOLS/name).write_bytes(data)
            (BASE/'start_native_reader.py').write_bytes(original_starter)
            subprocess.run(['docker','exec','-i','whalegirl-wechat-candidate-4118','python3','-c',
                "import pathlib,sys;pathlib.Path('/opt/tools/chat-select.py').write_bytes(sys.stdin.buffer.read())"],
                input=original_mounted, check=True, capture_output=True, timeout=8)
            shutil.copy2(backup/'settings.json', DATA/'settings.json')
            if (backup/'authorized-chats.json').exists():
                shutil.copy2(backup/'authorized-chats.json', JOBS/'authorized-chats.json')
            rollback = BASE/('current.moments-rollback-'+stamp)
            rollback.symlink_to(previous, target_is_directory=True)
            os.replace(rollback, BASE/'current')
            command('systemctl','restart','whalegirl-native-reader')
            command('systemctl','start','whalegirl-linux')
        else:
            api('config', {'settings':{'enabled':original['enabled']}})
        raise


if __name__ == '__main__':
    deploy()
