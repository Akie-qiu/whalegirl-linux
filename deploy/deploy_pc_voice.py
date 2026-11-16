"""Targeted, hash-guarded PC voice release. No database schema changes.

New release uses hardlinks for unchanged source; patched files are replaced with
new inodes so the rollback release is untouched. Never copies models or profiles.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.request

B = Path('/opt/whalegirl-linux-assets/pc-voice-20261111')
BASE = Path('/opt/whalegirl-linux')
DATA = Path('/var/lib/whalegirl-linux/core')
CURRENT = BASE / 'current'
PATCHED = ['whalegirl_linux/' + n for n in
           ('audio.py', 'audio_settings.py', 'audio_provider.py', 'audio_api.py', 'audio_tools.py', 'audio.js')]
NEW = ['deploy/pc_voice_bridge.py', 'tests/test_pc_voice.py']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    target = B / name
    target.write_text(json.dumps(value, indent=2))
    target.chmod(0o600)


def source_hashes(root):
    return {str(p.relative_to(root)): digest(p) for folder in ('whalegirl_linux', 'deploy')
            for p in (root / folder).rglob('*') if p.suffix in ('.py', '.js', '.css', '.html')}


def preserve_directory_owners(source, target):
    # copytree preserves mode, not uid/gid; a 0750 root-owned release otherwise
    # fails under the unprivileged service account before Python even starts.
    for directory in (target, *target.rglob('*')):
        if not directory.is_dir() or directory.is_symlink():
            continue
        original = source / directory.relative_to(target)
        metadata = original.stat() if original.exists() else source.stat()
        os.chown(directory, metadata.st_uid, metadata.st_gid)


def service_read_check(target):
    import pwd
    user = pwd.getpwnam('whalegirl-ai')
    subprocess.run(['/opt/whalegirl-linux/venv/bin/python', '-c',
        'from whalegirl_linux.audio_settings import AudioSettings; '
        'from whalegirl_linux.audio_api import install_audio_api; '
        'print("service-account import check passed")'], cwd=target,
        user=user.pw_uid, group=user.pw_gid, check=True)


def health():
    with urllib.request.urlopen('http://127.0.0.1:8092/health', timeout=5) as response:
        return json.load(response)


def switch(target):
    link = BASE / ('current-pc-voice-' + str(time.time_ns()))
    link.symlink_to(target)
    os.replace(link, CURRENT)


def prepare():
    B.mkdir(mode=0o700, exist_ok=True)
    assert not (B / 'deployment.json').exists(), 'Release already prepared'
    source = CURRENT.resolve()
    expected = json.loads((B / 'expected-source.json').read_text())
    assert str(source) == expected['current'], 'A newer release exists; stopped'
    for name in PATCHED:
        assert digest(source / name) == expected['hashes'][Path(name).name], name + ' changed since audit'
    target = BASE / 'releases' / ('pc-voice-' + time.strftime('%Y%m%d-%H%M%S'))
    hashes = source_hashes(source)
    shutil.copytree(source, target, copy_function=os.link, symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__', 'test-runs', 'artifacts', '.pytest_cache'))
    for name in PATCHED + NEW:
        patch = B / 'stage' / name
        assert patch.is_file() and patch.stat().st_size < 200000
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(destination.name + '.pc-new')
        temp.write_bytes(patch.read_bytes())
        stat = (source / name).stat() if (source / name).exists() else source.stat()
        os.chown(temp, stat.st_uid, stat.st_gid)
        temp.chmod(0o640)
        os.replace(temp, destination)
    preserve_directory_owners(source, target)
    service_read_check(target)
    db = sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True)
    try:
        db.row_factory = sqlite3.Row
        row = dict(db.execute('SELECT * FROM audio_config WHERE id=1').fetchone())
        save('audio-config-private-before.json', row)
    finally:
        db.close()
    save('deployment.json', {'source': str(source), 'target': str(target), 'source_hashes': hashes,
        'patch_hashes': {name: digest(target / name) for name in PATCHED + NEW},
        'private_hashes': {name: digest(DATA / name) for name in
                          ('settings.json', 'persona-cards.json', 'admin.token', 'gateway.token') if (DATA / name).exists()},
        'prepared': time.time(), 'health_before': health()})
    print(json.dumps({'prepared': str(target), 'patched_files': len(PATCHED), 'new_files': len(NEW),
                      'database_schema_changed': False, 'backup': str(B)}))


def activate():
    manifest = json.loads((B / 'deployment.json').read_text())
    assert CURRENT.resolve() == Path(manifest['source']), 'Current release changed; stopped'
    assert source_hashes(CURRENT.resolve()) == manifest['source_hashes'], 'Concurrent source edit; stopped'
    for name, expected in manifest['patch_hashes'].items():
        assert digest(Path(manifest['target']) / name) == expected
    for name, expected in manifest['private_hashes'].items():
        assert digest(DATA / name) == expected
    service_read_check(manifest['target'])
    db = sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True)
    try:
        db.row_factory = sqlite3.Row
        before = json.loads((B / 'audio-config-private-before.json').read_text())
        assert dict(db.execute('SELECT * FROM audio_config WHERE id=1').fetchone()) == before, 'Concurrent audio configuration edit; stopped'
        assert not db.execute("SELECT 1 FROM audio_jobs WHERE state IN ('generating','downloading','sending') LIMIT 1").fetchone(), 'Audio work active; defer deployment'
    finally:
        db.close()
    subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=True)
    switch(manifest['target'])
    subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=True)
    for _ in range(20):
        time.sleep(1)
        try:
            value = health()
            assert value.get('status') == 'ok' and not value.get('failed_background_tasks')
            save('activated.json', {'at': time.time(), 'health': value, 'current': str(CURRENT.resolve())})
            print(json.dumps({'activated': str(CURRENT.resolve()), 'health': value}))
            return
        except (OSError, ValueError, AssertionError):
            continue
    subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=True)
    switch(manifest['source'])
    subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=True)
    raise RuntimeError('Candidate health failed; previous release restored')


def rollback():
    manifest = json.loads((B / 'deployment.json').read_text())
    assert CURRENT.resolve() == Path(manifest['target']), 'Newer release exists; do not overwrite'
    row = json.loads((B / 'audio-config-private-before.json').read_text())
    known_revision = row['revision']
    for marker in ('configured.json', 'enabled-test-group.json', 'group-tools-enabled.json'):
        if (B / marker).exists():
            known_revision = max(known_revision, json.loads((B / marker).read_text())['revision'])
    check = sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True)
    try:
        assert check.execute('SELECT revision FROM audio_config WHERE id=1').fetchone()[0] == known_revision, 'Audio settings changed elsewhere; review before rollback'
    finally:
        check.close()
    subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=True)
    db = sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=rw', uri=True)
    try:
        db.row_factory = sqlite3.Row
        with db:
            current = dict(db.execute('SELECT * FROM audio_config WHERE id=1').fetchone())
            save('audio-config-private-at-rollback.json', current)
            # Cancel only new, not-yet-started PC speech. Never resubmit an
            # uncertain send, reset conversation data or restore an entire DB.
            if json.loads(current['settings_json']).get('speech_profile') == 'pc-voice':
                db.execute("UPDATE audio_jobs SET state='cancelled',error='电脑语音发布已回滚',updated=? "
                           "WHERE kind='speech' AND created>=? AND state='queued'", (time.time(), manifest['prepared']))
            db.execute('UPDATE audio_config SET revision=revision+1,settings_json=?,secrets_json=?,updated=? WHERE id=1',
                       (row['settings_json'], row['secrets_json'], time.time()))
    finally:
        db.close()
    switch(manifest['source'])
    subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=True)
    print(json.dumps({'rolled_back_to': str(CURRENT.resolve()), 'preserved_new_chats_and_memories': True}))


def repair_permissions():
    manifest = json.loads((B / 'deployment.json').read_text())
    assert CURRENT.resolve() == Path(manifest['source']), 'Current release changed; stopped'
    target, source = Path(manifest['target']), Path(manifest['source'])
    assert target.parent == BASE / 'releases' and target.name.startswith('pc-voice-')
    for name, expected in manifest['patch_hashes'].items():
        assert digest(target / name) == expected
    preserve_directory_owners(source, target)
    service_read_check(target)
    print('Candidate directory ownership repaired; rollback release left untouched')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['prepare', 'activate', 'rollback', 'repair_permissions'])
    globals()[parser.parse_args().operation]()
