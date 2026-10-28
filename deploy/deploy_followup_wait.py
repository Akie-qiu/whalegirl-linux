"""Deploy followup waits and bounded sticker retries without restarting native WeChat."""
import fcntl
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

BASE = Path('/opt/whalegirl-linux')
ASSETS = Path('/opt/whalegirl-linux-assets')
DATA = Path('/var/lib/whalegirl-linux/core')
CONTAINER = 'whalegirl-wechat-candidate-4118'
FILES = {'whalegirl_linux/' + name for name in ('runtime.py', 'store.py', 'gateway.py', 'safety.py', 'settings.py', 'upstream/ai_engine.py', 'habits.js', 'console.js', 'web.html')} | {'tests/' + name for name in ('test_chat_habits.py', 'test_media_delivery.py', 'test_model_controls.py')}
DEPLOYMENT = 'followup-wait'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def command(*args):
    return subprocess.check_output(args, text=True, timeout=25).strip()


def api(route):
    request = urllib.request.Request(
        'http://127.0.0.1:8092/api/' + route,
        headers={'Authorization': 'Bearer ' + (DATA / 'admin.token').read_text().strip()},
    )
    with OPENER.open(request, timeout=12) as response:
        return json.load(response)


def inflight():
    with sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True) as db:
        return sum(db.execute(query).fetchone()[0] for query in (
            "SELECT count(*) FROM outbound_actions WHERE state='sending'",
            "SELECT count(*) FROM picture_jobs WHERE state IN ('generating','sending')",
            "SELECT count(*) FROM moments_actions WHERE state='sending'",
        ))


def switch(target, stamp):
    current = BASE / 'current'
    pending = BASE / ('current.' + DEPLOYMENT + '-' + stamp)
    if pending.exists() or pending.is_symlink():
        raise RuntimeError('Pending release link already exists')
    pending.symlink_to(target, target_is_directory=True)
    os.replace(pending, current)


def main():
    lock = open(ASSETS / (DEPLOYMENT + '-deploy.lock'), 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manifest = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8-sig'))
    current = BASE / 'current'
    previous = current.resolve()
    if str(previous) != manifest['root'] or set(manifest['before']) != FILES or set(manifest['after']) != FILES:
        raise RuntimeError('Unexpected deployment manifest')

    def unchanged():
        if current.resolve() != previous:
            raise RuntimeError('Active release changed')
        for name, expected in manifest['before'].items():
            if digest(previous / name) != expected:
                raise RuntimeError('Production source changed: ' + name)

    unchanged()
    with tarfile.open(sys.argv[1]) as archive:
        members = archive.getmembers()
        if len(members) != len(FILES) or {member.name for member in members} != FILES:
            raise RuntimeError('Unexpected deployment archive')
        if not all(member.isfile() for member in members):
            raise RuntimeError('Non-file deployment entry')
        payload = {member.name: archive.extractfile(member).read() for member in members}
    for name, content in payload.items():
        if hashlib.sha256(content).hexdigest() != manifest['after'][name]:
            raise RuntimeError('Payload checksum mismatch: ' + name)
        if name.endswith('.py'):
            compile(content, name, 'exec')

    config_before = api('config')
    comparable_config = {key: value for key, value in config_before.items() if key != 'api_key_configured'}
    comparable_config.setdefault('followup_wait_enabled', True)
    comparable_config.setdefault('followup_wait_seconds', 10)
    state_before = api('state')
    if state_before.get('paused_reason') or state_before.get('counts', {}).get('uncertain'):
        raise RuntimeError('Resolve paused or uncertain sends first')
    if state_before.get('gateway', {}).get('status') != 'logged_in':
        raise RuntimeError('WeChat is offline; deployment postponed')
    if api('images').get('busy'):
        raise RuntimeError('Image work is active; deployment postponed')

    native_pid = command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat')
    reader_pid = command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value')
    container = command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER)
    protected_names = ('settings.json', 'api-key.enc', 'image-api-key.enc', 'admin.token')
    protected = {name: digest(DATA / name) for name in protected_names if (DATA / name).exists()}

    stamp = time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns() % 1_000_000_000)
    release = BASE / 'releases' / (DEPLOYMENT + '-' + stamp)
    backup = ASSETS / (DEPLOYMENT + '-before-' + stamp)
    backup.mkdir(mode=0o700)
    shutil.copytree(previous, release, symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__', 'test-runs', '.git'))
    for source in (previous, *previous.rglob('*')):
        target = release / source.relative_to(previous)
        if target.exists() or target.is_symlink():
            owner = source.lstat()
            os.chown(target, owner.st_uid, owner.st_gid, follow_symlinks=False)
    reference = (previous / 'whalegirl_linux/runtime.py').stat()
    for name, content in payload.items():
        target = release / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        owner = (previous / name).stat() if (previous / name).exists() else reference
        os.chown(target, owner.st_uid, owner.st_gid)
        target.chmod(owner.st_mode & 0o777)
        saved = backup / name
        if (previous / name).is_file():
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(previous / name, saved)

    test = subprocess.run(
        [str(BASE / 'venv/bin/python'), '-m', 'unittest',
         'test_chat_habits.ChatHabitTests.test_followup_wait_releases_worker_and_merges_new_message',
         'test_chat_habits.ChatHabitTests.test_followup_tool_keeps_original_burst_and_returns_result_to_model',
         'test_chat_habits.ChatHabitTests.test_disabling_followup_wait_restores_ordinary_reply',
         'test_chat_habits.ChatHabitTests.test_followup_timeout_resumes_once_without_another_wait',
         'test_media_delivery.GatewayDeliveryTests.test_sticker_preflight_read_retries_without_submitting_a_duplicate',
         'test_media_delivery.GatewayDeliveryTests.test_sticker_retries_definite_pre_send_failures_and_confirms',
         'test_media_delivery.GatewayDeliveryTests.test_uncertain_sticker_result_is_never_retried',
         'test_media_delivery.PartialDeliveryTests.test_three_definite_sticker_failures_pause_automatic_replies',
         'test_model_controls.ToolCallTests.test_chat_completions_returns_tool_call_and_usage'],
        cwd=release, env={**os.environ, 'PYTHONPATH': str(release) + ':' + str(release / 'tests')},
        capture_output=True, text=True, timeout=45,
    )
    (backup / 'focused-tests.log').write_text(test.stdout + test.stderr)
    if test.returncode:
        print((test.stdout + test.stderr)[-5000:], flush=True)
        raise RuntimeError('Focused followup and retry tests failed')

    started = time.monotonic()
    while api('state').get('processing') or inflight():
        if time.monotonic() - started > 90:
            raise RuntimeError('Core is busy; deployment postponed')
        time.sleep(1)
    unchanged()
    if any(digest(DATA / name) != value for name, value in protected.items()):
        raise RuntimeError('Configuration or credentials changed during staging')

    switched = stopped = False
    try:
        subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=True, timeout=30)
        stopped = True
        if inflight():
            raise RuntimeError('An outbound operation crossed the shutdown boundary')
        with sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True) as source:
            with sqlite3.connect(backup / 'state-before.sqlite3') as saved:
                source.backup(saved)
        (backup / 'state-before.sqlite3').chmod(0o600)
        switch(release, stamp)
        switched = True
        subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=True, timeout=30)

        for _ in range(60):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8092/health', timeout=2) as response:
                    health = json.load(response)
                state_after = api('state')
                config_after = api('config')
                config_after.pop('api_key_configured', None)
                if (health.get('status') == 'ok' and not health.get('failed_background_tasks')
                        and config_after == comparable_config
                        and state_after.get('gateway', {}).get('status') == 'logged_in'
                        and state_after.get('enabled') == bool(comparable_config.get('enabled'))):
                    break
            except Exception:
                pass
            time.sleep(.5)
        else:
            raise RuntimeError('New core did not become healthy with the existing login')

        preserved = {
            'wechat_pid': command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat') == native_pid,
            'reader_pid': command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value') == reader_pid,
            'container': command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER) == container,
            'configuration_and_keys': all(digest(DATA / name) == value for name, value in protected.items()),
        }
        if not all(preserved.values()):
            raise RuntimeError('Deployment preservation check failed: ' + str(preserved))
        report = {
            'release': str(release), 'previous': str(previous), 'backup': str(backup),
            'focused_tests': 9, 'health': health,
            'wechat_status': state_after.get('gateway', {}).get('status'),
            'bot_enabled': state_after.get('enabled'), 'preserved': preserved,
            'followup_wait_enabled': config_after.get('followup_wait_enabled'),
            'followup_wait_seconds': config_after.get('followup_wait_seconds'),
            'model_stream_enabled': config_after.get('model_stream_enabled'),
            'real_messages_sent': 0, 'hashes': manifest['after'],
        }
        (ASSETS / (DEPLOYMENT + '-deployment.json')).write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        print(json.dumps({key: value for key, value in report.items() if key != 'hashes'},
                         ensure_ascii=False), flush=True)
    except BaseException:
        if switched:
            subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=False, timeout=40)
            switch(previous, stamp + '-rollback')
        if stopped:
            subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=False, timeout=30)
        raise


if __name__ == '__main__':
    main()
