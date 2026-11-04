"""Atomic two-phase deployment for girlish persona unshackling, pipeline slimming and golden parameters."""
import ast
import copy
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
PYTHON = str(BASE / 'venv/bin/python')
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

SETTINGS_UPDATES = {
    'chat_short_reply_chars': 42,
    'chat_short_reply_sentences': 3,
    'persona_density': 'normal',
    'teasing': 'normal',
    'sticker_usage_target': 45,
    'max_tokens': 400,
    'model_temperature': 0.95,
}
TARGET_TEMPERATURE = 0.95


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value, owner=None):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf-8')
    path.chmod(0o600)
    if owner:
        os.chown(path, owner.st_uid, owner.st_gid)


def api(route):
    request = urllib.request.Request(
        'http://127.0.0.1:8092/' + route,
        headers={'Authorization': 'Bearer ' + (DATA / 'admin.token').read_text().strip()},
    )
    with OPENER.open(request, timeout=10) as response:
        return json.load(response)


def protected():
    return {
        p.name: sha(p)
        for p in DATA.iterdir()
        if p.is_file()
        and (
            p.suffix in ('.enc', '.key', '.token')
            or p.name in ('settings.json', 'model-profiles.json', 'persona-cards.json')
        )
    }


def tuning(release):
    spec = importlib.util.spec_from_file_location('one_time_persona_tuning', release / 'deploy/tune_active_persona.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.tune_prompt


def changed_data(release):
    settings = json.loads((DATA / 'settings.json').read_text('utf-8'))
    library = json.loads((DATA / 'persona-cards.json').read_text('utf-8'))
    profiles = json.loads((DATA / 'model-profiles.json').read_text('utf-8'))
    identity = settings['active_persona_id']
    row = next((r for r in library['cards'] if r['id'] == identity), None)
    if not row or row['revision'] != settings['active_persona_revision'] or row['card']['mode'] != 'raw':
        raise ValueError('Active card changed; inspect before synchronizing it')
    original_settings = copy.deepcopy(settings)
    original_library = copy.deepcopy(library)
    original_profiles = copy.deepcopy(profiles)

    prompt = tuning(release)(settings['system_prompt_override'])
    row['card']['raw_prompt'] = prompt
    row['revision'] += 1
    row['updated'] = time.time()
    settings.update(
        system_prompt_override=prompt,
        active_persona_revision=row['revision'],
        **SETTINGS_UPDATES,
    )

    target_cards = profiles.get('profiles') if isinstance(profiles.get('profiles'), dict) else profiles
    for pid, card in target_cards.items():
        if isinstance(card, dict):
            card['model_temperature'] = TARGET_TEMPERATURE
            card['max_tokens'] = 400

    allowed_setting_keys = {'system_prompt_override', 'active_persona_revision'} | set(SETTINGS_UPDATES)
    if {k: v for k, v in original_settings.items() if k not in allowed_setting_keys} != {
        k: v for k, v in settings.items() if k not in allowed_setting_keys
    }:
        raise RuntimeError('Unexpected settings edit')
    if [r for r in original_library['cards'] if r['id'] != identity] != [
        r for r in library['cards'] if r['id'] != identity
    ]:
        raise RuntimeError('An inactive card changed')
    orig_cards = original_profiles.get('profiles') if isinstance(original_profiles.get('profiles'), dict) else original_profiles
    if set(orig_cards) != set(target_cards):
        raise RuntimeError('Model profile keys changed unexpectedly')
    for pid in orig_cards:
        orig_card = orig_cards[pid]
        new_card = target_cards[pid]
        if isinstance(orig_card, dict):
            if {k: v for k, v in orig_card.items() if k not in ('model_temperature', 'max_tokens')} != {
                k: v for k, v in new_card.items() if k not in ('model_temperature', 'max_tokens')
            }:
                raise RuntimeError('Unexpected model profile field edit: ' + pid)

    return (
        settings,
        library,
        profiles,
        {
            'card_id': identity,
            'before_revision': original_settings['active_persona_revision'],
            'after_revision': row['revision'],
            'before_chars': len(original_settings['system_prompt_override']),
            'after_chars': len(prompt),
            'settings_updates': SETTINGS_UPDATES,
            'profiles_updated': sorted(target_cards.keys()),
        },
    )


def switch(release):
    pending = BASE / ('current-girlish-unshackled-' + str(time.time_ns()))
    pending.symlink_to(release, target_is_directory=True)
    os.replace(pending, BASE / 'current')


def prepare(folder):
    manifest = json.loads((folder / 'manifest.json').read_text('utf-8'))
    previous = Path(manifest['previous'])
    if (BASE / 'current').resolve() != previous:
        raise RuntimeError('Current release changed')
    if shutil.disk_usage(DATA).free < 2 * 1024**3:
        raise RuntimeError('Insufficient disk reserve')
    for name, digest in manifest['before'].items():
        if sha(previous / name) != digest:
            raise RuntimeError('Source changed: ' + name)
    protected_before = protected()
    with tarfile.open(folder / 'patch.tar.gz') as archive:
        members = archive.getmembers()
        if len(members) != len(manifest['after']) or {m.name for m in members} != set(manifest['after']):
            raise ValueError('Unexpected patch entries')
        if any(not m.isfile() or Path(m.name).is_absolute() or '..' in Path(m.name).parts for m in members):
            raise ValueError('Unsafe patch entry')
        payload = {m.name: archive.extractfile(m).read() for m in members}
    for name, raw in payload.items():
        if hashlib.sha256(raw).hexdigest() != manifest['after'][name]:
            raise ValueError('Patch checksum changed: ' + name)
        if name.endswith('.py'):
            ast.parse(raw.decode('utf-8-sig'), filename=name)
    release = BASE / 'releases' / ('girlish-unshackled-20261103-' + str(time.time_ns()))
    shutil.copytree(previous, release, symlinks=True, ignore=shutil.ignore_patterns('__pycache__', 'test-runs', '.git'))
    for name, raw in payload.items():
        target = release / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            raise ValueError('Refuse writing through a symlink')
        target.write_bytes(raw)
        target.chmod(0o644)
    service = pwd.getpwnam('whalegirl-ai')
    runs = release / 'test-runs'
    runs.mkdir()
    os.chown(runs, service.pw_uid, service.pw_gid)
    env = {
        **os.environ,
        'PYTHONPATH': str(release / 'tests'),
        'PYTHONDONTWRITEBYTECODE': '1',
        'LOGURU_LEVEL': 'ERROR',
    }
    with (folder / 'server-tests.log').open('w', encoding='utf-8') as log:
        result = subprocess.run(
            [
                'runuser',
                '-u',
                'whalegirl-ai',
                '--',
                PYTHON,
                '-m',
                'unittest',
                'test_tune_active_persona',
                'test_tune_girlish_settings',
                'test_persona_temperature',
                'test_affection',
                'test_chat_personality',
                'test_chat_habits',
                'test_proactive',
                'test_autonomy',
                'test_experience_growth',
                '-q',
            ],
            cwd=release,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=150,
        )
    if result.returncode:
        raise RuntimeError('Focused server checks failed: ' + (folder / 'server-tests.log').read_text('utf-8')[-500:])
    _, _, _, card = changed_data(release)
    if protected_before != protected():
        raise RuntimeError('Configuration changed during preparation')
    save(
        folder / 'prepared.json',
        {'release': str(release), 'previous': str(previous), 'protected': protected_before, 'card': card},
    )
    print(
        json.dumps(
            {
                'prepared': str(release),
                'card': card,
                'test_tail': (folder / 'server-tests.log').read_text('utf-8')[-180:],
            },
            ensure_ascii=False,
        )
    )


def inflight():
    with sqlite3.connect((DATA / 'state.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        return any(
            db.execute(sql).fetchone()[0]
            for sql in (
                "SELECT count(*) FROM outbound_actions WHERE state='sending'",
                "SELECT count(*) FROM picture_jobs WHERE state IN ('generating','sending')",
                "SELECT count(*) FROM moments_actions WHERE state='sending'",
            )
        )


def deploy(folder):
    prepared = json.loads((folder / 'prepared.json').read_text('utf-8'))
    previous, release = Path(prepared['previous']), Path(prepared['release'])
    if (BASE / 'current').resolve() != previous or protected() != prepared['protected']:
        raise RuntimeError('Release/config changed after preparation')
    manifest = json.loads((folder / 'manifest.json').read_text('utf-8'))
    for name, digest in manifest['after'].items():
        if sha(release / name) != digest:
            raise RuntimeError('Candidate changed: ' + name)
    for _ in range(30):
        if not api('api/state').get('processing') and not inflight():
            break
        time.sleep(1)
    else:
        raise RuntimeError('Core remains busy; deployment not started')
    values, library, profiles, card = changed_data(release)
    backup = Path('/var/backups/whalegirl') / ('girlish-unshackled-20261103-' + str(time.time_ns()))
    backup.mkdir(mode=0o700)
    originals = {}
    owners = {}
    for name in ('settings.json', 'persona-cards.json', 'model-profiles.json'):
        originals[name] = (DATA / name).read_bytes()
        owners[name] = (DATA / name).stat()
        (backup / name).write_bytes(originals[name])
        (backup / name).chmod(0o600)
    stopped = False
    try:
        subprocess.run(['systemctl', 'stop', 'whalegirl-linux'], check=True, timeout=40)
        stopped = True
        if inflight() or protected() != prepared['protected']:
            raise RuntimeError('State changed while stopping; configuration was not edited')
        for name, value in (
            ('persona-cards.json', library),
            ('model-profiles.json', profiles),
            ('settings.json', values),
        ):
            pending = DATA / (name + '.girlish-unshackled.pending')
            save(pending, value, owners[name])
            os.replace(pending, DATA / name)
        switch(release)
        subprocess.run(['systemctl', 'start', 'whalegirl-linux'], check=True, timeout=35)
        for _ in range(50):
            try:
                health = api('health')
                state = api('api/state')
                if (
                    health.get('status') == 'ok'
                    and not health.get('failed_background_tasks')
                    and state.get('gateway', {}).get('status') == 'logged_in'
                ):
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError('Updated core did not become ready')
        live = api('api/config')
        if (
            live.get('active_persona_revision') != card['after_revision']
            or live.get('system_prompt_override') != values['system_prompt_override']
            or live.get('model_temperature') != TARGET_TEMPERATURE
            or live.get('max_tokens') != 400
            or live.get('chat_short_reply_chars') != 42
            or live.get('chat_short_reply_sentences') != 3
            or live.get('persona_density') != 'normal'
            or live.get('teasing') != 'normal'
            or live.get('sticker_usage_target') != 45
        ):
            raise RuntimeError('Live configuration differs after activation')
        final = protected()
        if any(
            final.get(k) != v
            for k, v in prepared['protected'].items()
            if k not in ('settings.json', 'persona-cards.json', 'model-profiles.json')
        ):
            raise RuntimeError('Unexpected credentials edit')
        report = {
            'release': str(release),
            'previous': str(previous),
            'backup': str(backup),
            'card': card,
            'health': health,
            'wechat': state.get('gateway', {}).get('status'),
            'paused_reason': state.get('paused_reason'),
            'temperature': live.get('model_temperature'),
            'max_tokens': live.get('max_tokens'),
            'chat_short_reply_chars': live.get('chat_short_reply_chars'),
            'persona_density': live.get('persona_density'),
            'teasing': live.get('teasing'),
            'sticker_usage_target': live.get('sticker_usage_target'),
            'temperature_profiles': {
                k: v.get('model_temperature') for k, v in live.get('model_profiles', {}).items()
            },
            'credentials_unchanged': True,
            'native_wechat_restart': False,
            'database_restore': False,
            'free_bytes': shutil.disk_usage(DATA).free,
        }
        save(folder / 'deployment.json', report)
        print(json.dumps(report, ensure_ascii=False))
    except BaseException:
        if stopped:
            subprocess.run(['systemctl', 'stop', 'whalegirl-linux'], check=False, timeout=40)
            for name, raw in originals.items():
                pending = DATA / (name + '.girlish-unshackled-rollback.pending')
                pending.write_bytes(raw)
                pending.chmod(0o600)
                os.chown(pending, owners[name].st_uid, owners[name].st_gid)
                os.replace(pending, DATA / name)
            switch(previous)
            subprocess.run(['systemctl', 'start', 'whalegirl-linux'], check=False, timeout=35)
        raise


if __name__ == '__main__':
    folder = Path(sys.argv[1]).resolve(strict=True)
    if folder.parent != Path('/opt/whalegirl-linux-assets') or not folder.name.startswith('girlish-unshackled-20261103-'):
        raise ValueError('Unexpected staging folder')
    {'prepare': prepare, 'deploy': deploy}[sys.argv[2]](folder)
