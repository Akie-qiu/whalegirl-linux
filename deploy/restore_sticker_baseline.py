"""Restore the requested persona baseline while retaining later bug fixes."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

BASE = Path('/opt/whalegirl-linux')
ASSETS = Path('/opt/whalegirl-linux-assets')
DATA = Path('/var/lib/whalegirl-linux/core')
CONTAINER = 'whalegirl-wechat-candidate-4118'
RESTORE = {'whalegirl_linux/affection.py', 'whalegirl_linux/conversation.py', 'tests/test_affection.py'}
KEEP = {'whalegirl_linux/upstream/ai_engine.py', 'whalegirl_linux/model_reply.py',
        'whalegirl_linux/runtime.py', 'tests/test_content_filter.py'}
TESTS = (
    'test_affection', 'test_content_filter', 'test_three_bug_regressions.LengthBudgetTests',
    'test_model_controls', 'test_request_fingerprint', 'test_model_format_guard',
    'test_chat_pipeline_repair.EmptyResponseTests', 'test_media_delivery',
    'test_native_delivery', 'test_native_favorite_mapping', 'test_sticker_grid_safety',
    'test_sticker_misdelivery', 'test_sticker_scroll_progress',
    'test_sticker_subtree_delivery', 'test_ui_recovery',
)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def snapshot(root):
    result = {}
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if name not in {'__pycache__', '.git', 'test-runs', '.pytest_cache'})
        for name in [*names, *sorted(files)]:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                result[relative] = 'symlink:' + os.readlink(path)
            elif path.is_file():
                with path.open('rb') as stream:
                    result[relative] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def protected():
    names = ('settings.json', 'persona-cards.json', 'model-profiles.json', 'model-api-keys.enc',
             'api-key.enc', 'image-api-key.enc', 'admin.token', 'gateway.token',
             'encryption.key', 'bilibili-session.enc')
    return {name: hashlib.sha256((DATA / name).read_bytes()).hexdigest()
            for name in names if (DATA / name).is_file()}


def command(*arguments):
    return subprocess.check_output(arguments, text=True, timeout=25).strip()


def state():
    request = urllib.request.Request('http://127.0.0.1:8092/api/state', headers={
        'Authorization': 'Bearer ' + (DATA / 'admin.token').read_text().strip()})
    with OPENER.open(request, timeout=10) as response:
        return json.load(response)


def inflight():
    with sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True, timeout=5) as db:
        return sum(db.execute('SELECT count(*) FROM ' + table +
                   " WHERE state IN ('sending','generating')").fetchone()[0]
                   for table in ('outbound_actions', 'picture_jobs', 'moments_actions'))


def switch(target, stamp):
    pending = BASE / ('current.restore-sticker-' + stamp)
    if pending.exists() or pending.is_symlink():
        raise RuntimeError('Pending release link already exists')
    pending.symlink_to(target, target_is_directory=True)
    os.replace(pending, BASE / 'current')


def main(directory):
    audit = json.loads((directory / 'audit.json').read_text())
    previous = (BASE / 'current').resolve()
    target = Path(audit['target'])
    before, baseline = audit['source_snapshot'], audit['target_snapshot']
    if set(audit['differences']) != RESTORE | KEEP:
        raise RuntimeError('Unexpected version differences; review required')
    if str(previous) != audit['root'] or snapshot(previous) != before:
        raise RuntimeError('Live release changed since audit; preserving concurrent update')
    if snapshot(target) != baseline or protected() != audit['protected_hashes']:
        raise RuntimeError('Target release or protected configuration changed since audit')
    expected = {**before, **{name: baseline[name] for name in RESTORE}}
    if expected != {**baseline, **{name: before[name] for name in KEEP}}:
        raise RuntimeError('Candidate would include unreviewed version differences')
    free = shutil.disk_usage(DATA).free
    if free < 650 * 1024**2:
        raise RuntimeError('Insufficient free storage; no live changes')
    stamp = time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns() % 1_000_000_000)
    release = BASE / 'releases' / ('restored-sticker-persona-' + stamp)
    backup = ASSETS / ('before-restore-sticker-persona-' + stamp)
    backup.mkdir(mode=0o700)
    (backup / 'audit.json').write_text(json.dumps(audit, ensure_ascii=True, indent=2))
    shutil.copytree(previous, release, symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__', '.git', 'test-runs', '.pytest_cache'))
    for source in (previous, *previous.rglob('*')):
        destination = release / source.relative_to(previous)
        if destination.exists() or destination.is_symlink():
            owner = source.lstat()
            os.chown(destination, owner.st_uid, owner.st_gid, follow_symlinks=False)
    for name in RESTORE:
        saved = backup / name
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(previous / name, saved)
        content = (target / name).read_bytes()
        compile(content, name, 'exec')
        destination = release / name
        destination.write_bytes(content)
        owner = (previous / name).stat()
        os.chown(destination, owner.st_uid, owner.st_gid)
        destination.chmod(owner.st_mode & 0o777)
    tests = subprocess.run(
        [str(BASE / 'venv/bin/python'), '-m', 'unittest', *TESTS, '-q'], cwd=release,
        env={**os.environ, 'PYTHONPATH':str(release)+':'+str(release/'tests'),
             'WHALEGIRL_DATA_DIR':str(release/'test-runs'),
             'PYTHONDONTWRITEBYTECODE':'1', 'LOGURU_LEVEL':'ERROR'},
        capture_output=True, text=True, timeout=180,
    )
    (backup / 'tests.log').write_text(tests.stdout + tests.stderr)
    if tests.returncode:
        raise RuntimeError('Candidate tests failed; active release unchanged; see ' + str(backup / 'tests.log'))
    if snapshot(release) != expected:
        raise RuntimeError('Unexpected candidate file changes')
    if snapshot(previous) != before or (BASE/'current').resolve() != previous or protected() != audit['protected_hashes']:
        raise RuntimeError('Concurrent source/configuration changes detected; active release unchanged')
    deadline = time.monotonic() + 55
    while True:
        current = state()
        if current.get('gateway', {}).get('status') != 'logged_in' or current.get('paused_reason'):
            raise RuntimeError('WeChat is offline or sending paused; active release unchanged')
        if not current.get('processing') and not inflight():
            break
        if time.monotonic() > deadline:
            raise RuntimeError('Core remains busy; active release unchanged')
        time.sleep(2)
    wechat_pid = command('docker','exec',CONTAINER,'pgrep','-x','wechat')
    container = command('docker','inspect','--format','{{.Id}} {{.RestartCount}}',CONTAINER)
    native_before = command('docker','exec',CONTAINER,'sha256sum','/opt/tools/chat-select.py').split()[0]
    if native_before != before['deploy/chat_select_linux.py']:
        raise RuntimeError('Mounted selector does not match preserved bug fix')
    stopped = switched = False
    try:
        subprocess.run(['systemctl','stop','whalegirl-linux.service'],check=True,timeout=30)
        stopped = True
        if (BASE/'current').resolve() != previous or snapshot(previous) != before or protected() != audit['protected_hashes']:
            raise RuntimeError('Concurrent changes detected immediately before switch')
        if snapshot(release) != expected or snapshot(target) != baseline:
            raise RuntimeError('Candidate or requested baseline changed before switch')
        switch(release, stamp)
        switched = True
        subprocess.run(['systemctl','start','whalegirl-linux.service'],check=True,timeout=30)
        deadline = time.monotonic() + 45
        while True:
            try:
                with OPENER.open('http://127.0.0.1:8092/health', timeout=2) as response:
                    health = json.load(response)
                current = state()
                if (health.get('status') == 'ok' and not health.get('failed_background_tasks')
                        and current.get('gateway',{}).get('status') == 'logged_in'
                        and not current.get('paused_reason')):
                    break
            except (OSError, ValueError):
                pass
            if time.monotonic() > deadline:
                raise RuntimeError('Post-switch health verification failed')
            time.sleep(1)
        main_pid = command('systemctl','show','-p','MainPID','--value','whalegirl-linux.service')
        preserved = {
            'protected_configuration_and_persona':protected() == audit['protected_hashes'],
            'complete_candidate_source':snapshot(release) == expected,
            'previous_release_unchanged':snapshot(previous) == before,
            'requested_baseline_unchanged':snapshot(target) == baseline,
            'restored_files_match_requested_version':all(snapshot(release).get(name) == baseline[name] for name in RESTORE),
            'latest_error_handling':all(snapshot(release).get(name) == before[name] for name in KEEP),
            'wechat_process':command('docker','exec',CONTAINER,'pgrep','-x','wechat') == wechat_pid,
            'wechat_container':command('docker','inspect','--format','{{.Id}} {{.RestartCount}}',CONTAINER) == container,
            'native_sticker_picker':command('docker','exec',CONTAINER,'sha256sum','/opt/tools/chat-select.py').split()[0] == native_before,
            'active_release':(BASE/'current').resolve() == release,
            'running_core_working_directory':Path('/proc/'+main_pid+'/cwd').resolve() == release,
        }
        if not all(preserved.values()):
            raise RuntimeError('Post-switch source/runtime preservation check failed')
        report = {
            'release':str(release), 'previous':str(previous), 'requested_baseline':str(target),
            'backup':str(backup), 'restored_files':sorted(RESTORE), 'retained_bugfix_files':sorted(KEEP),
            'unchanged_entries':len(before)-len(RESTORE), 'health':health,
            'wechat_status':current.get('gateway',{}).get('status'),
            'send_paused':bool(current.get('paused_reason')), 'preserved':preserved,
            'tests_tail':(tests.stdout+tests.stderr).splitlines()[-5:],
            'disk_free_before':free, 'disk_free_after':shutil.disk_usage(DATA).free,
            'database_restore_performed':False, 'configuration_changed':False,
            'real_wechat_tests_sent':0, 'checked_at':time.time(),
        }
        (directory/'deployment.json').write_text(json.dumps(report,ensure_ascii=True,indent=2))
        print(json.dumps(report,ensure_ascii=True),flush=True)
    except BaseException:
        if switched and (BASE/'current').resolve() == release and snapshot(release) == expected:
            subprocess.run(['systemctl','stop','whalegirl-linux.service'],check=False,timeout=30)
            switch(previous, stamp+'-rollback')
        if stopped:
            subprocess.run(['systemctl','start','whalegirl-linux.service'],check=False,timeout=30)
        raise


if __name__ == '__main__':
    with (ASSETS/'content-filter-deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        main(Path(sys.argv[1]))
