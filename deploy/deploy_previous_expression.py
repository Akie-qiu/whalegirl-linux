"""Small, authorized patch to the actual previous release; no new chat engine."""
import ast
import hashlib
import importlib.util
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

BASE = Path('/opt/whalegirl-linux')
DATA = Path('/var/lib/whalegirl-linux/core')
PREVIOUS = BASE / 'releases/humanization-bugs-20261101-20261101-201156-558562461'
REJECTED = BASE / 'releases/autonomous-chat-canary-20261102-232455'
PYTHON = str(BASE / 'venv/bin/python')
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf-8'); path.chmod(0o600)


def api(route):
    req = urllib.request.Request('http://127.0.0.1:8092/' + route,
        headers={'Authorization': 'Bearer ' + (DATA / 'admin.token').read_text().strip()})
    with OPENER.open(req, timeout=10) as response:
        return json.load(response)


def protected():
    return {p.name: sha(p) for p in DATA.iterdir() if p.is_file() and
            (p.suffix in ('.enc', '.key', '.token') or p.name in ('settings.json', 'model-profiles.json', 'persona-cards.json'))}


def cleanup_module(folder):
    spec = importlib.util.spec_from_file_location('scoped_forgetting', folder / 'forget_test_chat.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def switch(release):
    pending = BASE / ('current-expression-' + str(time.time_ns()))
    pending.symlink_to(release, target_is_directory=True); os.replace(pending, BASE / 'current')


def prepare(folder):
    manifest = json.loads((folder / 'manifest.json').read_text('utf-8'))
    if (BASE / 'current').resolve() != PREVIOUS:
        raise RuntimeError('Current release is no longer the reviewed previous version')
    for name, digest in manifest['before'].items():
        if sha(PREVIOUS / name) != digest:
            raise RuntimeError('Previous source changed: ' + name)
    if shutil.disk_usage(DATA).free < 2 * 1024**3:
        raise RuntimeError('Insufficient disk reserve')
    with tarfile.open(folder / 'patch.tar.gz') as archive:
        entries = archive.getmembers()
        if len(entries) != len(manifest['after']) or {m.name for m in entries} != set(manifest['after']):
            raise ValueError('Unexpected archive entries')
        if any(not m.isfile() or Path(m.name).is_absolute() or '..' in Path(m.name).parts for m in entries):
            raise ValueError('Unsafe archive path')
        payload = {m.name: archive.extractfile(m).read() for m in entries}
    for name, raw in payload.items():
        if hashlib.sha256(raw).hexdigest() != manifest['after'][name]:
            raise ValueError('Patch checksum changed')
        if name.endswith('.py'):
            ast.parse(raw.decode('utf-8-sig'))
    staged = folder / 'candidate-path.json'
    if staged.exists():
        release = Path(json.loads(staged.read_text())['release']).resolve(strict=True)
        if release.parent != (BASE / 'releases').resolve() or not release.name.startswith('previous-expression-20261103-'):
            raise ValueError('Unexpected reusable candidate')
    else:
        release = BASE / 'releases' / ('previous-expression-20261103-' + str(time.time_ns()))
        shutil.copytree(PREVIOUS, release, symlinks=True,
                        ignore=shutil.ignore_patterns('__pycache__', 'test-runs', '.git'))
        save(staged, {'release': str(release)})
    for name, raw in payload.items():
        target = release / name; target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            raise RuntimeError('Refuse writing through a symlink')
        target.write_bytes(raw); target.chmod(0o644)
    service = pwd.getpwnam('whalegirl-ai')
    runs = release / 'test-runs'; runs.mkdir(exist_ok=True); os.chown(runs, service.pw_uid, service.pw_gid)
    env = {**os.environ, 'PYTHONPATH': str(release / 'tests'), 'PYTHONDONTWRITEBYTECODE': '1', 'LOGURU_LEVEL': 'ERROR'}
    with (folder / 'server-tests.log').open('w') as log:
        result = subprocess.run(['runuser', '-u', 'whalegirl-ai', '--', PYTHON, '-m', 'unittest',
            'test_expression_and_forgetting', 'test_context_delivery', 'test_memory_hub', 'test_native_delivery', '-q'],
            cwd=release, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=120)
    if result.returncode:
        raise RuntimeError('Previous-release patch checks failed; see server-tests.log')
    preview = cleanup_module(folder).run()
    save(folder / 'prepared.json', {'release': str(release), 'protected': protected(), 'cleanup': preview})
    print(json.dumps({'prepared': str(release), 'test_tail': (folder / 'server-tests.log').read_text()[-150:],
                      'cleanup': preview}, ensure_ascii=False))


def deploy(folder):
    manifest = json.loads((folder / 'manifest.json').read_text())
    prepared = json.loads((folder / 'prepared.json').read_text()); release = Path(prepared['release'])
    if (BASE / 'current').resolve() != PREVIOUS or protected() != prepared['protected']:
        raise RuntimeError('Release/configuration changed since preparation')
    for name, digest in manifest['after'].items():
        if sha(release / name) != digest:
            raise RuntimeError('Candidate changed after checks')
    state = api('api/state')
    if state.get('processing'):
        raise RuntimeError('Core is busy; deployment has not started')
    with sqlite3.connect((DATA / 'state.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        if any(db.execute(q).fetchone()[0] for q in (
            "SELECT count(*) FROM outbound_actions WHERE state='sending'",
            "SELECT count(*) FROM picture_jobs WHERE state IN ('generating','sending')",
            "SELECT count(*) FROM moments_actions WHERE state='sending'")):
            raise RuntimeError('Native sending/image work is in progress')
    settings = DATA / 'settings.json'; owner = settings.stat(); original = settings.read_bytes()
    (folder / 'settings-before-expression.json').write_bytes(original)
    (folder / 'settings-before-expression.json').chmod(0o600)
    values = json.loads(original)
    new_fields = {'girlish_tone': 'bright', 'chat_short_replies_enabled': True,
                  'chat_short_reply_sentences': 2, 'chat_short_reply_chars': 40}
    values.update(new_fields)
    stopped = False
    try:
        subprocess.run(['systemctl', 'stop', 'whalegirl-linux'], check=True, timeout=40); stopped = True
        cleanup = cleanup_module(folder).run(apply=True, expected=prepared['cleanup']['plan_sha256'],
            output=Path('/var/backups/whalegirl') / ('forget-test-chat-20261103-' + str(time.time_ns())))
        pending = DATA / 'settings-expression.pending'
        save(pending, values); os.chown(pending, owner.st_uid, owner.st_gid); os.replace(pending, settings)
        switch(release)
        subprocess.run(['systemctl', 'start', 'whalegirl-linux'], check=True, timeout=35)
        for _ in range(50):
            try:
                health = api('health'); state = api('api/state')
                if health.get('status') == 'ok' and not health.get('failed_background_tasks'):
                    break
            except Exception:
                pass
            time.sleep(.5)
        else:
            raise RuntimeError('Patched previous release did not become healthy')
        final = protected()
        if any(final.get(k) != v for k, v in prepared['protected'].items() if k != 'settings.json'):
            raise RuntimeError('Unexpected credentials/persona/model card change')
        report = {'release': str(release), 'baseline': str(PREVIOUS), 'health': health,
                  'wechat': state.get('gateway', {}).get('status'), 'paused_reason': state.get('paused_reason'),
                  'settings': new_fields, 'cleanup': cleanup, 'free_bytes': shutil.disk_usage(DATA).free,
                  'core_persona_unchanged': True, 'native_wechat_restart': False}
        save(folder / 'deployment.json', report)
        print(json.dumps(report, ensure_ascii=False))
    except BaseException:
        if stopped:
            subprocess.run(['systemctl', 'stop', 'whalegirl-linux'], check=False, timeout=40)
            pending = DATA / 'settings-expression-failed.pending'; pending.write_bytes(original)
            pending.chmod(0o600); os.chown(pending, owner.st_uid, owner.st_gid); os.replace(pending, settings)
            switch(PREVIOUS)
            subprocess.run(['systemctl', 'start', 'whalegirl-linux'], check=False, timeout=35)
        raise


def retire(folder):
    report = json.loads((folder / 'deployment.json').read_text())
    if (BASE / 'current').resolve() != Path(report['release']) or api('health').get('status') != 'ok':
        raise RuntimeError('Restored core is not healthy; retirement refused')
    target = REJECTED.resolve(strict=True)
    if REJECTED.is_symlink() or target.parent != (BASE / 'releases').resolve() or target == PREVIOUS.resolve():
        raise ValueError('Unexpected recursive deletion target')
    if target.name != 'autonomous-chat-canary-20261102-232455':
        raise ValueError('Deletion target is not the explicitly reported rejected release')
    for proc in Path('/proc').iterdir():
        if proc.name.isdigit():
            try:
                if (proc / 'cwd').resolve().is_relative_to(target):
                    raise RuntimeError('A process still uses the rejected release')
            except (OSError, ValueError):
                pass
    size = sum(p.stat().st_size for p in target.rglob('*') if p.is_file() and not p.is_symlink())
    shutil.rmtree(target)
    report.update(rejected_release_removed=str(target), retired_bytes=size, free_bytes=shutil.disk_usage(DATA).free)
    save(folder / 'deployment.json', report)
    print(json.dumps({'rejected_release_removed': str(target), 'retired_bytes': size,
                      'free_bytes': report['free_bytes']}, ensure_ascii=False))


if __name__ == '__main__':
    folder = Path(sys.argv[1]).resolve(strict=True)
    if folder.parent != Path('/opt/whalegirl-linux-assets') or folder.name != 'previous-expression-20261103':
        raise ValueError('Unexpected staging directory')
    {'prepare': prepare, 'deploy': deploy, 'retire': retire}[sys.argv[2]](folder)
