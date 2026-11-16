"""Guarded final connectivity refinement of this task's own release only."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import time

from deploy_pc_voice import B, CURRENT, DATA, digest, health, save, service_read_check


def main():
    manifest = json.loads((B / 'deployment.json').read_text())
    target = CURRENT.resolve()
    assert str(target) == manifest['target']
    for name, expected in manifest['patch_hashes'].items():
        assert digest(target / name) == expected, 'Concurrent edit: ' + name
    files = ['whalegirl_linux/audio.py', 'whalegirl_linux/audio_provider.py',
             'whalegirl_linux/audio_tools.py', 'tests/test_pc_voice.py']
    backup = B / 'before-connectivity-refinement'
    assert not backup.exists(), 'Refinement already attempted; inspect saved result'
    backup.mkdir(mode=0o700)
    for name in files:
        old = target / name
        destination = backup / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old, destination)
        temp = old.with_name(old.name + '.refined')
        temp.write_bytes((B / 'stage' / name).read_bytes())
        temp.chmod(old.stat().st_mode & 0o777)
        os.chown(temp, old.stat().st_uid, old.stat().st_gid)
        os.replace(temp, old)
    service_read_check(target)
    command = ['systemd-run', '--quiet', '--wait', '--pipe', '--unit=whalegirl-pc-voice-final-tests-20261111',
               '-p', 'CPUQuota=50%', '-p', 'MemoryMax=256M', '-p', 'TasksMax=96', '-p', 'RuntimeMaxSec=120',
               '--working-directory=' + str(target), '--setenv=PYTHONPATH=' + str(target / 'tests'),
               '/opt/whalegirl-linux/venv/bin/python', '-m', 'unittest', 'test_audio', 'test_pc_voice',
               'test_voice_worker', 'test_native_voice_compat']
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    (B / 'final-linux-tests.log').write_text(result.stdout)
    if result.returncode:
        for name in files:
            old = target / name
            metadata = old.stat()
            temp = old.with_name(old.name + '.restore')
            temp.write_bytes((backup / name).read_bytes())
            os.chown(temp, metadata.st_uid, metadata.st_gid)
            temp.chmod(metadata.st_mode & 0o777)
            os.replace(temp, old)
        print(result.stdout[-2000:])
        raise RuntimeError('Refinement tests failed; original task files restored, no restart')
    import sqlite3
    db = sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True)
    try:
        assert not db.execute("SELECT 1 FROM audio_jobs WHERE state IN ('generating','downloading','sending') LIMIT 1").fetchone()
    finally:
        db.close()
    for name in files:
        manifest['patch_hashes'][name] = digest(target / name)
    save('deployment.json', manifest)
    subprocess.run(['systemctl', 'restart', 'whalegirl-linux.service'], check=True)
    for _ in range(25):
        time.sleep(1)
        try:
            value = health()
            if value.get('status') == 'ok' and not value.get('failed_background_tasks'):
                print(result.stdout[-120:])
                save('refinement-result.json', {'health': value, 'at': time.time(), 'tests': 72})
                print(json.dumps({'refined': str(target), 'tests': 72, 'health': value}))
                return
        except (OSError, ValueError):
            continue
    raise RuntimeError('Refined release did not pass health; use guarded deployment rollback')


if __name__ == '__main__':
    main()
