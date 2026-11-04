"""Update five console assets atomically per file, with no service restart."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request

DATA = Path('/var/lib/whalegirl-linux/core')
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
FILES = {'whalegirl_linux/' + n for n in ('improvements.js', 'affection.js', 'console.js', 'console.css', 'web.html')}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def configs():
    return {p.name: digest(p.read_bytes()) for p in DATA.iterdir() if p.is_file() and
            (p.suffix in ('.enc', '.key', '.token') or p.name in ('settings.json', 'model-profiles.json', 'persona-cards.json'))}


def get(route):
    req = urllib.request.Request('http://127.0.0.1:8092/' + route,
        headers={'Authorization': 'Bearer ' + (DATA / 'admin.token').read_text().strip()})
    with OPENER.open(req, timeout=12) as response:
        return response.read()


def main(folder):
    if folder.parent != Path('/opt/whalegirl-linux-assets') or not folder.name.startswith('expression-console-20261103-'):
        raise ValueError('Unexpected staging directory')
    manifest = json.loads((folder / 'manifest.json').read_text('utf-8'))
    root = Path('/opt/whalegirl-linux/current').resolve(strict=True)
    if str(root) != manifest['release'] or set(manifest['before']) != FILES or set(manifest['after']) != FILES:
        raise RuntimeError('Current release differs from the reviewed target')
    if shutil.disk_usage(DATA).free < 2 * 1024**3:
        raise RuntimeError('Disk reserve below 2GiB')
    with tarfile.open(folder / 'console.tar.gz') as tar:
        entries = tar.getmembers()
        if len(entries) != len(FILES) or {m.name for m in entries} != FILES or any(not m.isfile() for m in entries):
            raise ValueError('Unexpected console archive')
        payload = {m.name: tar.extractfile(m).read() for m in entries}
    original = {}
    owners = {}
    for name in FILES:
        target = root / name
        if target.is_symlink() or not target.resolve().is_relative_to(root):
            raise ValueError('Refuse writing outside the release')
        original[name] = target.read_bytes(); owners[name] = target.stat()
        if digest(original[name]) != manifest['before'][name] or digest(payload[name]) != manifest['after'][name]:
            raise RuntimeError('Source checksum changed: ' + name)
    before = configs()
    pid = subprocess.check_output(['systemctl', 'show', 'whalegirl-linux', '--property=MainPID', '--value'], text=True).strip()
    backup = folder / 'before'; backup.mkdir(mode=0o700)
    for name, raw in original.items():
        target = backup / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw); target.chmod(0o600)

    def write(name, raw):
        target = root / name; owner = owners[name]
        pending = target.with_name(target.name + '.expression-ui.pending')
        pending.write_bytes(raw); pending.chmod(stat.S_IMODE(owner.st_mode))
        os.chown(pending, owner.st_uid, owner.st_gid); os.replace(pending, target)

    try:
        # Load the new HTML/cache tags only after the compatible scripts exist.
        for name in ('whalegirl_linux/improvements.js', 'whalegirl_linux/affection.js',
                     'whalegirl_linux/console.js', 'whalegirl_linux/console.css', 'whalegirl_linux/web.html'):
            write(name, payload[name])
        for name in FILES:
            route = '' if name.endswith('web.html') else Path(name).name
            if digest(get(route)) != manifest['after'][name]:
                raise RuntimeError('Served console asset differs: ' + name)
        health = json.loads(get('health')); state = json.loads(get('api/state'))
        current_pid = subprocess.check_output(['systemctl', 'show', 'whalegirl-linux', '--property=MainPID', '--value'], text=True).strip()
        if current_pid != pid or health.get('status') != 'ok':
            raise RuntimeError('Core changed or is not healthy')
        report = {'release': str(root), 'backup': str(backup), 'files': len(FILES),
                  'served_asset_hashes_verified': True, 'config_unchanged': configs() == before,
                  'core_pid_unchanged': True, 'core_restart': False, 'wechat_restart': False,
                  'wechat': state.get('gateway', {}).get('status'),
                  'background_failures': health.get('failed_background_tasks'),
                  'free_bytes': shutil.disk_usage(DATA).free, 'created': time.time(),
                  'browser_acceptance_skipped_per_user': True}
        (folder / 'deployment.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
        (folder / 'deployment.json').chmod(0o600)
        print(json.dumps(report, ensure_ascii=False))
    except BaseException:
        for name, raw in original.items():
            write(name, raw)
        raise


if __name__ == '__main__':
    main(Path(sys.argv[1]).resolve(strict=True))
