"""Apply a hash checked core-only reference-image release, retaining backups."""
import hashlib
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

DATA = Path('/var/lib/whalegirl-linux/core')
BASE = Path('/opt/whalegirl-linux')
ASSETS = Path('/opt/whalegirl-linux-assets')
CONTAINER = 'whalegirl-wechat-candidate-4118'
FILES = {'whalegirl_linux/images.py', 'whalegirl_linux/image_references.py', 'whalegirl_linux/images.js',
         'whalegirl_linux/images.css', 'whalegirl_linux/app.py', 'whalegirl_linux/settings.py', 'REFERENCE_IMAGES.md'}


def api(path, body=None):
    token = (DATA / 'admin.token').read_text().strip()
    request = urllib.request.Request('http://127.0.0.1:8092/api/' + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def command(*args):
    return subprocess.check_output(args, text=True, timeout=25).strip()


def install(path, data):
    owner = (path if path.exists() else path.parent).stat()
    pending = path.with_name(path.name + '.reference-pending')
    pending.write_bytes(data)
    os.chown(pending, owner.st_uid, owner.st_gid)
    pending.chmod(owner.st_mode & 0o777 if path.exists() else 0o640)
    pending.replace(path)


def main():
    import fcntl
    lock = open(ASSETS / 'reference-deploy.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manifest = json.loads(Path(sys.argv[2]).read_text())
    root = (BASE / 'current').resolve()
    if str(root) != manifest['root']:
        raise RuntimeError('Release changed before deployment')
    for name, expected in manifest['before'].items():
        path = root / name
        current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        if current != expected:
            raise RuntimeError('Source changed before deployment: ' + name)
    with tarfile.open(sys.argv[1]) as archive:
        members = archive.getmembers()
        expected = FILES | {f'references/{n:02d}.jpg' for n in range(1, 8)}
        if {m.name for m in members} != expected or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected reference deployment contents')
        contents = {m.name: archive.extractfile(m).read() for m in members}
    for name, data in contents.items():
        if hashlib.sha256(data).hexdigest() != manifest['after'][name]:
            raise RuntimeError('Deployment content checksum failed: ' + name)
        if name.endswith('.py'):
            compile(data, name, 'exec')
    if api('state').get('processing'):
        raise RuntimeError('Chat processing in progress; defer deployment')
    with sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True) as db:
        for table, states in [('outbound_actions', ('sending',)), ('moments_actions', ('sending',)),
                              ('picture_jobs', ('generating', 'sending'))]:
            marks = ','.join('?' for _ in states)
            if db.execute(f'SELECT count(*) FROM {table} WHERE state IN ({marks})', states).fetchone()[0]:
                raise RuntimeError('Outbound work in progress: ' + table)
    native_before = command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat')
    reader_before = command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value')
    backup = ASSETS / ('references-before-' + time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(mode=0o700)
    previous = {}
    for index, name in enumerate(sorted(FILES)):
        path = root / name
        if path.exists():
            previous[name] = path.read_bytes()
            shutil.copy2(path, backup / (str(index) + '-' + path.name))
    shutil.copy2(DATA / 'settings.json', backup / 'settings.json')
    with sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True) as source:
        with sqlite3.connect(backup / 'state.sqlite3') as target:
            source.backup(target)
    subprocess.run(['systemctl', 'stop', 'whalegirl-linux'], check=True, timeout=30)
    try:
        if (BASE / 'current').resolve() != root:
            raise RuntimeError('Release changed during deployment')
        for name in FILES:
            install(root / name, contents[name])
        sys.path.insert(0, str(root))
        from types import SimpleNamespace
        from whalegirl_linux.image_references import ReferenceLibrary
        settings = json.loads((DATA / 'settings.json').read_text())
        library = ReferenceLibrary(SimpleNamespace(directory=DATA, settings=SimpleNamespace(account_id=settings['account_id'])))
        presets = [
            ('chibi', 'Q版女仆', '大头小身体、圆润可爱的Q版；参考脸、蓝发蓝眼、鲸尾与女仆服'),
            ('chibi', 'Q版冬装三视图', 'Q版冬季服饰和正侧背面细节；不要把三视图画成三个人'),
            ('girl', '少女版女仆三视图一', '少女套图的服饰和正侧背面细节；身体比例与本套其他参考图协调'),
            ('girl', '少女版女仆三视图二', '少女版主要比例、发型与正侧背面造型；保留鲸尾和服饰辨识特征'),
            ('girl', '少女版表现图', '少女版主要面容、蓝发渐变、表情与整体比例；按要求改变动作和场景'),
            ('mature', '偏年长版女仆表现图', '同一角色偏年长的视觉版本，比例更修长、气质更成熟，保留鲸鱼主题'),
            ('mature', '偏年长版冬装三视图', '偏年长版冬季服饰与正侧背面造型；不要与Q版或少女版比例混合'),
        ]
        for index, (variant, name, role) in enumerate(presets, 1):
            library.add(contents[f'references/{index:02d}.jpg'], variant, name, role)
        owner = pwd.getpwnam('whalegirl-ai')
        references_root = DATA / 'pictures' / 'references'
        for path in [references_root, *references_root.rglob('*')]:
            os.chown(path, owner.pw_uid, owner.pw_gid)
        subprocess.run(['systemctl', 'start', 'whalegirl-linux'], check=True, timeout=30)
        for _ in range(40):
            try:
                state = api('images')
                break
            except Exception:
                time.sleep(.5)
        else:
            raise RuntimeError('Reference image API did not start')
        # Patch only this choice against the latest live settings.
        public = api('images/config', {'values': {'image_character_variant': 'girl'}})
        if command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat') != native_before:
            raise RuntimeError('Native WeChat process unexpectedly changed')
        if command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value') != reader_before:
            raise RuntimeError('Native reader unexpectedly changed')
        result = {'at': time.time(), 'release': str(root), 'backup': str(backup),
                  'wechat_pid_preserved': True, 'reader_pid_preserved': True,
                  'core_enabled': public['enabled'], 'default_variant': public['image_character_variant'],
                  'reference_count': len(state['references']['items']), 'hashes': manifest['after']}
        (ASSETS / 'references-deployment.json').write_text(json.dumps(result, indent=2))
        print(json.dumps({k: v for k, v in result.items() if k != 'hashes'}))
    except Exception:
        subprocess.run(['systemctl', 'stop', 'whalegirl-linux'], check=False, timeout=30)
        for name, data in previous.items():
            install(root / name, data)
        # New, unused modules and all reference data remain for inspection; no deletion.
        subprocess.run(['systemctl', 'start', 'whalegirl-linux'], check=False, timeout=30)
        raise


if __name__ == '__main__':
    main()
