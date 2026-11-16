"""Owner-authorized pinned native-client upgrade, with an untouched old profile.

Run prepare, then activate, or rollback. Never erases containers or databases.
Only settings.py is changed in a new copy of the current core release.
"""
import hashlib
import gzip
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

B = Path('/opt/whalegirl-linux-assets/voice-repair-20261111')
BASE = Path('/opt/whalegirl-linux')
DATA = Path('/var/lib/whalegirl-linux/core')
GATE = Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway')
OLD_HOME = Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/home')
NEW_HOME = Path('/var/lib/whalegirl-linux/candidate-4.1.13.26/home')
NAME = 'whalegirl-wechat-candidate-4118'
OLD = 'whalegirl-wechat-prevoice-4118-20261111'
IMAGE = 'whalegirl-wechat:4.1.13.26-voice-20261111'
VERSION = '4.1.13.26'
PACKAGE_SHA = 'a0db8caeee8d1e6cfe0aa5ce58710270f8d74a90ff4920f53a3fc22aa0956fe9'


def run(*args, **kw):
    return subprocess.check_output(args, text=True, timeout=kw.pop('timeout', 45), **kw).strip()


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def write(path, data, mode=0o600):
    path = Path(path)
    pending = path.with_name(path.name + '.voice-pending')
    pending.write_bytes(data if isinstance(data, bytes) else data.encode())
    pending.chmod(mode)
    if path.exists():
        old = path.stat()
        os.chown(pending, old.st_uid, old.st_gid)
    pending.replace(path)


def save(name, value):
    write(B / name, json.dumps(value, ensure_ascii=False, indent=2))


def api(path):
    token = (DATA / 'admin.token').read_text().strip()
    request = urllib.request.Request('http://127.0.0.1:8092' + path,
                                     headers={'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)


def source_hashes(root):
    return {str(p.relative_to(root)): sha(p) for p in (root / 'whalegirl_linux').rglob('*')
            if p.is_file() and p.suffix in ('.py', '.js', '.css', '.html')}


def prepare():
    if (B / 'upgrade-manifest.json').exists():
        raise RuntimeError('Prepared manifest already exists; inspect it rather than rebuilding')
    package = B / 'WeChatLinux_x86_64.deb'
    assert package.stat().st_size == 231861976 and sha(package) == PACKAGE_SHA
    assert run('dpkg-deb', '--field', str(package), 'Version') == VERSION
    current = (BASE / 'current').resolve()
    assert current.parent == BASE / 'releases'
    info = json.loads(run('docker', 'inspect', NAME))[0]
    assert info['State']['Running']
    assert api('/health')['failed_background_tasks'] == 0
    before = B / 'private-before'
    before.mkdir(mode=0o700, exist_ok=True)
    for key, path in {'settings.json': DATA / 'settings.json',
                      'start_native_reader.py': BASE / 'start_native_reader.py',
                      'runtime-version.json': GATE / 'runtime-version.json'}.items():
        shutil.copy2(path, before / key)
        (before / key).chmod(0o600)
    if (BASE / 'native-runtime.json').exists():
        shutil.copy2(BASE / 'native-runtime.json', before / 'native-runtime.json')
    save('container-before.json', info)
    entry = run('docker', 'exec', NAME, 'cat', '/opt/gateway_entry.py') + '\n'
    assert entry.count('VERSION = "4.1.1.8"') == 1
    entry = entry.replace('VERSION = "4.1.1.8"', 'VERSION = "4.1.13.26"')
    needle = 'version = subprocess.check_output(["dpkg-query", "-W", "-f=${Version}", "wechat"], text=True).strip()'
    assert entry.count(needle) == 1
    entry = entry.replace(needle, 'version = json.loads(Path("/opt/voice-package.json").read_text())["version"]')
    marker = 'spawn(["/usr/bin/wechat"], as_wechat=True)'
    assert entry.count(marker) == 1
    entry = entry.replace(marker, '''# An input device must exist when Qt enumerates microphones at startup.
subprocess.run(["runuser", "-u", "wechat", "--", "env", "HOME=/home/wechat",
                "/opt/tools/voice-audio-ready"], env=env, check=True, timeout=25)
''' + marker)
    write(B / 'gateway_entry_voice.py', entry, 0o644)
    # Avoid Docker layer export on this 2-core host. The official package is
    # extracted once under resource limits and mounted read-only. The base OS
    # image's dpkg metadata stays unchanged; actual binary/package hashes and
    # runtime-version.json identify the running client, rather than that metadata.
    root = B / 'package-root'
    assert not root.exists(), 'Extracted package already exists; inspect before retrying'
    with (B / 'extract.log').open('w') as log:
        subprocess.run(['systemd-run', '--wait', '--pipe', '--collect',
                        '--unit=whalegirl-voice-unpack-' + str(time.time_ns()),
                        '-p', 'CPUQuota=40%', '-p', 'MemoryMax=384M', '-p', 'IOWeight=20',
                        '/usr/bin/env', 'DPKG_DEB_THREADS_MAX=1', 'dpkg-deb', '--extract', str(package), str(root)],
                       stdout=log, stderr=log, check=True, timeout=600)
    binary = root / 'opt/wechat/wechat'
    write(B / 'wechat-pinned.sha256', sha(binary) + '  /opt/wechat/wechat\n', 0o644)
    write(B / 'voice-package.json', json.dumps({'version': VERSION, 'package_sha256': PACKAGE_SHA,
                                               'binary_sha256': sha(binary)}), 0o644)
    stub = run('docker', 'exec', NAME, 'cat', '/opt/wechat/RadiumWMPF/runtime/WeChatAppEx')
    assert len(stub) < 4096
    write(B / 'wechat_appex_stub', stub + '\n', 0o755)
    (B / 'voice_send_worker.py').chmod(0o755)
    release = BASE / 'releases' / ('native-voice-' + time.strftime('%Y%m%d-%H%M%S'))
    hashes = source_hashes(current)
    shutil.copytree(current, release, ignore=shutil.ignore_patterns('test-runs', '__pycache__', '.pytest_cache', '.git'))
    source = release / 'whalegirl_linux/settings.py'
    text = source.read_text()
    needle = 'Literal["4.1.0.16", "4.1.1.8"]'
    assert text.count(needle) == 1
    write(source, text.replace(needle, 'Literal["4.1.0.16", "4.1.1.8", "4.1.13.26"]'), 0o644)
    (release / 'deploy').mkdir(exist_ok=True)
    (release / 'tests').mkdir(exist_ok=True)
    for name in ('voice_send_worker.py', 'VOICE_WORKER_LICENSE.txt'):
        shutil.copy2(B / name, release / 'deploy' / name)
    shutil.copy2(B / 'test_voice_worker.py', release / 'tests/test_voice_worker.py')
    owner = current.stat()
    for path in [release, *release.rglob('*')]:
        if not path.is_symlink():
            os.chown(path, owner.st_uid, owner.st_gid)
    with (B / 'linux-tests.log').open('w') as log:
        subprocess.run([str(BASE / 'venv/bin/python'), '-m', 'unittest', 'discover', '-s', 'tests',
                        '-p', 'test_voice_worker.py', '-v'], cwd=release, stdout=log, stderr=log, check=True, timeout=90)
        subprocess.run([str(BASE / 'venv/bin/python'), '-m', 'unittest', 'discover', '-s', 'tests',
                        '-p', 'test_audio.py', '-q'], cwd=release, stdout=log, stderr=log, check=True, timeout=120)
    assert source_hashes(current) == hashes, 'Current source changed; candidate not activated'
    manifest = dict(previous=str(current), release=str(release), source_hashes=hashes,
                    settings_source_before=sha(current / 'whalegirl_linux/settings.py'),
                    starter_before=sha(BASE / 'start_native_reader.py'), image=info['Image'], version=VERSION,
                    package_root=str(root), binary_sha256=sha(binary), mode='verified-bind-package',
                    old_container=OLD, original_container=NAME, new_home=str(NEW_HOME), prepared=True,
                    protected={p.name: sha(p) for p in DATA.iterdir() if p.is_file() and
                               (p.suffix in ('.enc', '.token') or p.name == 'persona-cards.json')})
    save('upgrade-manifest.json', manifest)
    print(json.dumps({'prepared': True, 'image': info['Image'], 'release': str(release),
                      'tests': 49, 'free_bytes': shutil.disk_usage('/').free}), flush=True)


def clone_home():
    if NEW_HOME.exists():
        # Reuse only this explicitly retained, stopped compatibility trial.
        # Never merge a second copy into it or touch the rollback profile.
        names = run('docker', 'ps', '-a', '--format', '{{.Names}}').splitlines()
        trials = [name for name in names if name.startswith('whalegirl-wechat-voice-inactive-')]
        assert trials, 'Candidate home has no retained trial container; refusing reuse'
        for name in names:
            info = json.loads(run('docker', 'inspect', name))[0]
            if any(mount['Source'] == str(NEW_HOME) for mount in info['Mounts']):
                assert not info['State']['Running'], 'Candidate profile is already open elsewhere'
        return
    def ignore(directory, names):
        rel = Path(directory).relative_to(OLD_HOME)
        if rel == Path('.'):
            return {'.cache'} & set(names)
        if rel == Path('.xwechat'):
            return {'log', 'crashinfo'} & set(names)
        if rel == Path('.xwechat/radium'):
            return {'xworker'} & set(names)
        if len(rel.parts) == 2 and rel.parts[0] == 'xwechat_files' and rel.parts[1].startswith('wxid_'):
            return {'cache'} & set(names)
        return set()
    def owned_copy(source, target):
        shutil.copy2(source, target)
        st = os.stat(source, follow_symlinks=False)
        os.chown(target, st.st_uid, st.st_gid, follow_symlinks=False)
        return target
    NEW_HOME.parent.mkdir(mode=0o700, exist_ok=True)
    shutil.copytree(OLD_HOME, NEW_HOME, symlinks=True, copy_function=owned_copy, ignore=ignore)
    for directory, dirs, files in os.walk(NEW_HOME):
        target = Path(directory)
        st = (OLD_HOME / target.relative_to(NEW_HOME)).stat()
        os.chown(target, st.st_uid, st.st_gid)


def switch(release):
    link = BASE / 'current.voice-pending'
    assert not link.exists()
    link.symlink_to(release)
    link.replace(BASE / 'current')


def rollback():
    m = json.loads((B / 'upgrade-manifest.json').read_text())
    current = (BASE / 'current').resolve()
    assert current in (Path(m['release']), Path(m['previous'])), 'A newer release is active; rollback refused'
    assert source_hashes(Path(m['previous'])) == m['source_hashes'], 'Rollback source changed; inspect before restoring'
    subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service', 'whalegirl-native-reader.service'], check=True)
    old_info = subprocess.run(['docker', 'inspect', OLD], capture_output=True, text=True)
    if old_info.returncode == 0:
        candidate_info = subprocess.run(['docker', 'inspect', NAME], capture_output=True, text=True)
        if candidate_info.returncode == 0:
            subprocess.run(['docker', 'stop', '--time', '20', NAME], check=True)
            abandoned = 'whalegirl-wechat-voice-inactive-' + str(time.time_ns())
            subprocess.run(['docker', 'rename', NAME, abandoned], check=True)
        subprocess.run(['docker', 'rename', OLD, NAME], check=True)
    settings = json.loads((DATA / 'settings.json').read_text())
    settings['expected_wechat_version'] = json.loads((B / 'private-before/settings.json').read_text())['expected_wechat_version']
    write(DATA / 'settings.json', json.dumps(settings, ensure_ascii=False, indent=2))
    write(BASE / 'start_native_reader.py', (B / 'private-before/start_native_reader.py').read_bytes(), 0o644)
    runtime = BASE / 'native-runtime.json'
    if (B / 'private-before/native-runtime.json').is_file():
        write(runtime, (B / 'private-before/native-runtime.json').read_bytes())
    elif runtime.exists():
        runtime.rename(B / ('native-runtime.inactive-' + str(time.time_ns()) + '.json'))
    switch(Path(m['previous']))
    subprocess.run(['docker', 'start', NAME], check=True, stdout=subprocess.DEVNULL)
    time.sleep(8)
    subprocess.run(['systemctl', 'reset-failed', 'whalegirl-native-reader.service', 'whalegirl-linux.service'], check=True)
    subprocess.run(['systemctl', 'start', 'whalegirl-native-reader.service', 'whalegirl-linux.service'], check=True)
    save('rollback.json', {'at': time.time(), 'release': m['previous'], 'database_restored': False,
                           'old_home_preserved': True, 'new_home_retained': NEW_HOME.exists()})
    print(json.dumps({'rollback_completed': True}), flush=True)


def activate():
    m = json.loads((B / 'upgrade-manifest.json').read_text())
    assert (BASE / 'current').resolve() == Path(m['previous'])
    assert source_hashes(Path(m['previous'])) == m['source_hashes'], 'Live code changed; no activation'
    assert sha(BASE / 'start_native_reader.py') == m['starter_before']
    required_free = 700_000_000 if NEW_HOME.exists() else 1_600_000_000
    assert shutil.disk_usage('/').free > required_free, 'Not enough space for the candidate and headroom'
    state = api('/api/state')
    assert not state.get('processing') and not state.get('paused_reason')
    with sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True) as db:
        assert not db.execute("SELECT 1 FROM outbound_actions WHERE state='sending' LIMIT 1").fetchone()
        for table in ('audio_jobs', 'picture_jobs', 'moments_actions'):
            assert not db.execute('SELECT 1 FROM ' + table + " WHERE state IN ('sending','generating','downloading') LIMIT 1").fetchone()
    # This is the only interruption boundary. The old client and its raw
    # profile remain available; new WeChat never opens the old profile.
    subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service', 'whalegirl-native-reader.service'], check=True)
    try:
        subprocess.run(['docker', 'stop', '--time', '20', NAME], check=True, stdout=subprocess.DEVNULL)
        # A complete snapshot was already closed successfully before the first
        # activation's gateway backup check. Preserve and reuse it; rollback
        # never replaces the live core database, including subsequent events.
        backup = B / 'state-before-native-upgrade.sql.gz'
        if not backup.exists():
            with sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True) as source:
                source.execute('BEGIN')
                assert source.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
                with backup.open('xb') as stream:
                    backup.chmod(0o600)
                    with gzip.GzipFile(fileobj=stream, mode='wb', compresslevel=1) as compressed:
                        for line in source.iterdump():
                            compressed.write((line + '\n').encode())
        assert backup.stat().st_size > 100_000_000
        # The gateway database is encrypted. Its server is now stopped, so a
        # byte-for-byte copy plus retained WAL is consistent; never open it as
        # an unencrypted SQLite database or try to migrate it here.
        for source in GATE.glob('agent.db*'):
            target = B / ('gateway-before-' + str(time.time_ns()) + '-' + source.name)
            shutil.copy2(source, target)
            target.chmod(0o600)
        clone_home()
        assert shutil.disk_usage('/').free > 600_000_000
        subprocess.run(['docker', 'rename', NAME, OLD], check=True)
        info = json.loads((B / 'container-before.json').read_text())
        args = ['docker', 'run', '--detach', '--name', NAME, '--label', 'com.whalegirl.role=wechat-voice',
                '--restart', 'unless-stopped', '--no-healthcheck', '--read-only', '--network', 'bridge',
                '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m,mode=1777', '--tmpfs', '/run:rw,nosuid,nodev,size=32m',
                '--security-opt', 'seccomp=unconfined', '--cap-add', 'SYS_PTRACE', '--memory', '1280m',
                '--memory-swap', '2560m', '--cpus', '1', '--pids-limit', '1024', '--shm-size', '128m',
                '--publish', '127.0.0.1:6175:6174', '--env', 'AGENT_WECHAT_TOKEN=']
        for mount in info['Mounts']:
            target = mount['Destination']
            source = str(NEW_HOME) if target == '/home/wechat' else mount['Source']
            args += ['--mount', 'type=bind,src=' + source + ',dst=' + target + (',readonly' if not mount['RW'] else '')]
        extra_mounts = {
            str(Path(m['package_root']) / 'opt/wechat'): '/opt/wechat',
            str(B / 'gateway_entry_voice.py'): '/opt/gateway_entry.py',
            str(B / 'wechat-pinned.sha256'): '/opt/wechat-pinned.sha256',
            str(B / 'voice-package.json'): '/opt/voice-package.json',
            str(B / 'voice_send_worker.py'): '/opt/tools/voice-send-worker',
            str(B / 'wechat_appex_stub'): '/opt/wechat/RadiumWMPF/runtime/WeChatAppEx',
            str(GATE / 'native-tools/chat_select_linux.py'): '/opt/tools/chat-select.py',
            str(GATE / 'native-tools/a11y_safe.py'): '/opt/tools/a11y-dump',
            str(GATE / 'native-tools/health-guard-bin'): '/usr/local/sbin',
        }
        for source, target in extra_mounts.items():
            assert Path(source).exists()
            args += ['--mount', 'type=bind,src=' + source + ',dst=' + target + ',readonly']
        args.append(m['image'])
        run(*args)
        deadline = time.monotonic() + 50
        while time.monotonic() < deadline:
            try:
                version = json.loads((GATE / 'runtime-version.json').read_text())
                if version['wechat_version'] == VERSION:
                    break
            except (OSError, ValueError):
                pass
            time.sleep(1)
        else:
            raise RuntimeError('New native client did not become ready')
        write(BASE / 'native-runtime.json', json.dumps({'wechat_version': VERSION, 'mode': m['mode'],
                                                       'base_image_sha256': m['image'],
                                                       'binary_sha256': m['binary_sha256'],
                                                       'owner_authorized_at': time.time()}))
        write(BASE / 'start_native_reader.py', (B / 'start_native_reader.py').read_bytes(), 0o644)
        settings = json.loads((DATA / 'settings.json').read_text())
        settings['expected_wechat_version'] = VERSION
        write(DATA / 'settings.json', json.dumps(settings, ensure_ascii=False, indent=2))
        switch(Path(m['release']))
        subprocess.run(['systemctl', 'reset-failed', 'whalegirl-native-reader.service', 'whalegirl-linux.service'], check=True)
        subprocess.run(['systemctl', 'start', 'whalegirl-native-reader.service', 'whalegirl-linux.service'], check=True)
        deadline = time.monotonic() + 50
        while time.monotonic() < deadline:
            try:
                health = api('/health')
                if health['status'] == 'ok' and not health['failed_background_tasks']:
                    break
            except Exception:
                pass
            time.sleep(1)
        else:
            raise RuntimeError('Core service did not become healthy')
        state = api('/api/state')
        assert all(sha(DATA / name) == digest for name, digest in m['protected'].items())
        save('activation.json', {'at': time.time(), 'release': m['release'], 'version': VERSION,
                                 'health': health, 'wechat': state.get('gateway', {}).get('status'),
                                 'paused': bool(state.get('paused_reason')), 'free_bytes': shutil.disk_usage('/').free,
                                 'old_raw_profile_preserved': True, 'credentials_preserved': True,
                                 'model_calls': 0, 'voice_test_sent': False})
        print((B / 'activation.json').read_text(), flush=True)
    except BaseException:
        rollback()
        raise


if __name__ == '__main__':
    {'prepare': prepare, 'activate': activate, 'rollback': rollback}[sys.argv[1]]()
