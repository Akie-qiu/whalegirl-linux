"""Targeted, hash-guarded maintenance; never restarts the WeChat client."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import urllib.request

CURRENT = Path('/opt/whalegirl-linux/current')
DATA = Path('/var/lib/whalegirl-linux/core')
B = Path('/opt/whalegirl-linux-assets/voice-dynamics-moments-20261111')
EXPECTED = {
    'whalegirl_linux/audio.py': '1699ea228a24c2a16d0d6fcaa7079237eec97e0307e2ec674e5c0d1f454ab254',
    'whalegirl_linux/audio_settings.py': '5ede04f5f2708453db9cd21ce82dc959b52c7ea28d97874b976fe82cdca907be',
    'whalegirl_linux/audio_tools.py': '81522bc78654c76bbf4633ac3108c56851354746e7243e91368d3933bd0adba1',
    'whalegirl_linux/audio_provider.py': 'e089cc46caa3e542843291962bec1a3c9bb7ec9b46bbecb08139aeafc1160c2e',
    'whalegirl_linux/audio.js': 'f834f140d7f5ca1e8b77af93dd36eff9145b05d480a1411c6c97e5ed35f9156b',
    'deploy/pc_voice_bridge.py': 'b26c572ec957c89bb7a50fbb9ad5ab1d90864ac4dfdecfbfef3d39a4e815f596',
    'whalegirl_linux/moments.py': 'cdcf8445ba9e1eb2399275ab55aab0fcbeb70a887322aab63310e8217ff3c52c',
    'whalegirl_linux/moments.js': 'c30baad69b7b93e40d7fb40e419c61a688bfccae407770c8036aca48424dfe28',
    'whalegirl_linux/images.py': 'd9bc62017ef7c762220caf92f9bfdbcf40348d890126c69d2fe19fcb27d93dac',
}
FILES = [*EXPECTED, 'tests/test_voice_dynamics.py', 'tests/test_moments_provider_recovery.py']
TESTS = ['test_audio', 'test_pc_voice', 'test_pc_voice_control', 'test_voice_dynamics',
    'test_voice_worker', 'test_native_voice_compat', 'test_moments', 'test_moments_world',
    'test_moments_story', 'test_moments_references', 'test_moments_recovery',
    'test_moments_navigation_repair', 'test_moments_scroll', 'test_moments_provider_recovery',
    'test_images', 'test_image_canvas', 'test_image_result_protocol', 'test_image_tools',
    'test_image_retention', 'test_chat_world_images', 'test_picture_receipts']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    path = B / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    path.chmod(0o600)


def api(path, value=None):
    token = (DATA / 'admin.token').read_text().strip()
    request = urllib.request.Request('http://127.0.0.1:8092' + path,
        data=json.dumps(value).encode() if value is not None else None,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def replace(source, target, stat):
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + '.voice-dynamics-new')
    temp.write_bytes(source.read_bytes())
    os.chown(temp, stat.st_uid, stat.st_gid)
    temp.chmod(0o640)
    os.replace(temp, target)  # Break hardlinks; preserve all rollback releases.


def readonly():
    return sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True)


def prepare():
    root = CURRENT.resolve()
    assert root.name == 'pc-voice-20261111-183639', 'Newer release exists'
    assert not (B / 'manifest.json').exists(), 'Backup already prepared'
    assert all(digest(root / n) == h for n, h in EXPECTED.items()), 'Concurrent source update'
    assert shutil.disk_usage('/').free > 700 * 1024**2, 'Insufficient staging headroom'
    B.mkdir(mode=0o700, exist_ok=True)
    for name in EXPECTED:
        target = B / 'before' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, target)
    for name in ('settings.json', 'persona-cards.json'):
        shutil.copy2(DATA / name, B / ('before-' + name))
        (B / ('before-' + name)).chmod(0o600)
    with readonly() as db:
        row = db.execute('SELECT revision,settings_json FROM audio_config WHERE id=1').fetchone()
    save('before-audio.json', {'revision': row[0], 'settings_json': row[1]})
    for directory in ('whalegirl_linux', 'tests', 'deploy', 'styles'):
        shutil.copytree(root / directory, B / 'check' / directory, copy_function=os.link,
            ignore=shutil.ignore_patterns('__pycache__', 'test-runs', 'artifacts', '.pytest_cache'))
    (B / 'check/test-runs').mkdir()
    for name in FILES:
        replace(B / 'stage' / name, B / 'check' / name,
            (root / name).stat() if (root / name).exists() else root.stat())
    hashes = {str(p.relative_to(root)): digest(p) for p in (root / 'whalegirl_linux').rglob('*')
        if p.is_file() and p.suffix in ('.py', '.js', '.html', '.css')}
    private = {n: digest(DATA / n) for n in ('settings.json', 'persona-cards.json',
        'admin.token', 'gateway.token', 'api-key.enc', 'image-api-key.enc') if (DATA / n).exists()}
    save('manifest.json', {'root': str(root), 'before_hashes': EXPECTED, 'source_hashes': hashes,
        'private_hashes': private, 'after_hashes': {n: digest(B / 'stage' / n) for n in FILES},
        'prepared': time.time()})
    print(json.dumps({'prepared': True, 'backup': str(B), 'production_changed': False}))


def test():
    command = ['systemd-run', '--quiet', '--wait', '--pipe', '--collect',
        '-p', 'CPUQuota=50%', '-p', 'MemoryMax=256M', '-p', 'WorkingDirectory=' + str(B / 'check'),
        '-E', 'PYTHONPATH=' + str(B / 'check/tests'), '/opt/whalegirl-linux/venv/bin/python',
        '-m', 'unittest', *TESTS, '-q']
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300)
    (B / 'linux-tests.log').write_text(result.stdout)
    print(result.stdout[-5000:])
    assert result.returncode == 0, 'Linux tests failed; live code unchanged'
    save('tests-passed.json', {'at': time.time(), 'returncode': 0, 'modules': TESTS})


def activate():
    m = json.loads((B / 'manifest.json').read_text())
    root = Path(m['root'])
    assert CURRENT.resolve() == root, 'Release changed'
    assert (B / 'tests-passed.json').exists()
    for name, value in {**m['source_hashes'], **EXPECTED}.items():
        assert digest(root / name) == value, 'Concurrent change: ' + name
    assert all(digest(DATA / n) == h for n, h in m['private_hashes'].items()), 'Configuration changed'
    assert all(digest(B / 'stage' / n) == h for n, h in m['after_hashes'].items())
    with readonly() as db:
        assert not db.execute("SELECT 1 FROM audio_jobs WHERE state IN ('generating','downloading','sending') LIMIT 1").fetchone(), 'Audio work active'
        assert not db.execute("SELECT 1 FROM moments_actions WHERE state IN ('generating','sending') LIMIT 1").fetchone(), 'Moments work active'
    state = api('/api/state')
    assert not state.get('processing'), 'Chat work active'
    subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=True)
    try:
        for name in FILES:
            replace(B / 'stage' / name, root / name, (root / name).stat() if (root / name).exists() else root.stat())
        subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=True)
        for _ in range(30):
            time.sleep(1)
            try:
                health = api('/health')
                if health.get('status') == 'ok' and not health.get('failed_background_tasks'):
                    save('activated.json', {'at': time.time(), 'health': health})
                    print(json.dumps({'activated': True, 'health': health}))
                    return
            except (OSError, ValueError):
                pass
        raise RuntimeError('Health check failed')
    except BaseException:
        subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=True)
        for name in EXPECTED:
            replace(B / 'before' / name, root / name, (root / name).stat())
        subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=True)
        raise


def configure():
    assert (B / 'activated.json').exists()
    before = json.loads((B / 'before-audio.json').read_text())
    audio = api('/api/audio')
    assert audio['revision'] == before['revision'], 'Concurrent audio settings update'
    core = api('/api/config')
    old = json.loads((B / 'before-settings.json').read_text())
    assert core['image_model'] == old['image_model'] == 'gpt-image-2.6', 'Image setting changed'
    audio['settings']['ai_voice_dynamics_enabled'] = True
    result = api('/api/audio/config', {'revision': audio['revision'], 'settings': audio['settings']})
    save('configured.json', {'at': time.time(), 'audio_revision': result['revision'],
        'image_model': core['image_model'], 'image_size': core['image_size'], 'stage': 'audio_enabled'})
    image = api('/api/images/config', {'values': {'image_model': 'gpt-image-2.5-1k'}})
    assert image['image_size'] == old['image_size']
    save('configured.json', {'at': time.time(), 'audio_revision': result['revision'],
        'image_model': image['image_model'], 'image_size': image['image_size'], 'stage': 'complete'})
    print(json.dumps({'configured': True, 'audio_revision': result['revision'],
        'ai_voice_dynamics_enabled': result['settings']['ai_voice_dynamics_enabled'],
        'image_model': image['image_model'], 'image_size': image['image_size']}))


def rollback():
    m = json.loads((B / 'manifest.json').read_text())
    root = Path(m['root'])
    assert CURRENT.resolve() == root, 'Newer release exists'
    assert all(digest(root / n) == h for n, h in m['after_hashes'].items()), 'Newer source patch exists'
    changed = json.loads((B / 'configured.json').read_text()) if (B / 'configured.json').exists() else None
    if changed:
        assert api('/api/audio')['revision'] == changed['audio_revision'], 'Newer voice settings exist'
        assert api('/api/config')['image_model'] == changed['image_model'], 'Newer image model selected'
        old_settings = json.loads((B / 'before-settings.json').read_text())
        api('/api/images/config', {'values': {'image_model': old_settings['image_model']}})
    subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=True)
    if changed:
        # Offline targeted compatibility rollback: retain jobs, secrets, histories.
        before = json.loads((B / 'before-audio.json').read_text())
        with sqlite3.connect(DATA / 'state.sqlite3') as db:
            row = db.execute('SELECT revision,settings_json,updated FROM audio_config WHERE id=1').fetchone()
            assert row[0] == changed['audio_revision']
            db.execute('INSERT OR IGNORE INTO audio_config_revisions VALUES(?,?,?)', row)
            db.execute('UPDATE audio_config SET revision=?,settings_json=?,updated=? WHERE id=1 AND revision=?',
                (row[0] + 1, before['settings_json'], time.time(), row[0]))
    for name in EXPECTED:
        replace(B / 'before' / name, root / name, (root / name).stat())
    subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=True)
    save('rolled-back.json', {'at': time.time()})
    print(json.dumps({'rolled_back': True, 'chat_memory_and_new_media_retained': True}))


def cap_images():
    """Enforce the existing 1K output limit after a real provider returned 1254px."""
    m = json.loads((B / 'manifest.json').read_text())
    root = Path(m['root'])
    assert CURRENT.resolve() == root
    names = ['whalegirl_linux/images.py', 'tests/test_moments_provider_recovery.py']
    assert all(digest(root / n) == m['after_hashes'][n] for n in FILES), 'Newer patch exists'
    for name in names:
        replace(B / 'stage' / name, B / 'check' / name, (root / name).stat())
    result = subprocess.run(['/opt/whalegirl-linux/venv/bin/python', '-m', 'unittest',
        'test_moments_provider_recovery', 'test_images', 'test_image_canvas', '-q'],
        cwd=B / 'check', env={**os.environ, 'PYTHONPATH': str(B / 'check/tests')},
        capture_output=True, text=True, timeout=60)
    (B / 'image-cap-tests.log').write_text(result.stdout + result.stderr)
    assert result.returncode == 0, (result.stdout + result.stderr)[-2000:]
    with readonly() as db:
        assert not db.execute("SELECT 1 FROM picture_jobs WHERE state='generating' LIMIT 1").fetchone()
        assert not db.execute("SELECT 1 FROM audio_jobs WHERE state IN ('generating','downloading','sending') LIMIT 1").fetchone()
    assert not api('/api/state').get('processing'), 'Chat work active'
    for name in names:
        target = B / 'before-image-cap' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, target)
    subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=True)
    try:
        for name in names:
            assert digest(root / name) == m['after_hashes'][name]
            replace(B / 'stage' / name, root / name, (root / name).stat())
        subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=True)
        for _ in range(25):
            time.sleep(1)
            try:
                if api('/health').get('status') == 'ok':
                    for name in names:
                        m['after_hashes'][name] = digest(root / name)
                    save('manifest.json', m)
                    save('image-cap-verified.json', {'at': time.time(), 'max_generated_edge': 1024,
                        'regressions': (result.stdout + result.stderr)[-400:]})
                    print(json.dumps({'image_cap_active': True, 'max_edge': 1024,
                        'tests': (result.stdout + result.stderr)[-400:]}))
                    return
            except (OSError, ValueError):
                pass
        raise RuntimeError('Health check failed')
    except BaseException:
        subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=True)
        for name in names:
            replace(B / 'before-image-cap' / name, root / name, (root / name).stat())
        subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=True)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['prepare', 'test', 'activate', 'configure', 'cap_images', 'rollback'])
    globals()[parser.parse_args().operation]()
