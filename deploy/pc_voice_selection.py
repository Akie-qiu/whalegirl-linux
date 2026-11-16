"""Two curated voice IDs, one serial model worker, explicit switch ownership."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pc_voice_common import read_control, read_worker, write_json
from pc_voice_engine import voice_catalog, load_engine

PENDING = {'queued', 'stopping', 'loading', 'rolling_back'}


def selection_status(root):
    try:
        value = json.loads((Path(root) / 'voice-selection.json').read_text(encoding='utf-8'))
        return {key: value.get(key) for key in ('id', 'voice_id', 'phase', 'error', 'at', 'updated')}
    except (OSError, ValueError, TypeError):
        return {}


@contextmanager
def selection_lock(root):
    path = Path(root) / 'voice-selection.lock'
    with path.open('a+b') as stream:
        if path.stat().st_size == 0:
            stream.write(b'\0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def request_switch(root, voice_id, request_id=None):
    root = Path(root)
    entries = {row['id']: row for row in voice_catalog(root)}
    if voice_id not in entries:
        raise ValueError('该声线尚未安装或不在允许列表中')
    request_id = request_id or uuid.uuid4().hex
    if not re.fullmatch('[0-9a-f]{32}', request_id):
        raise ValueError('无效的切换任务标识')
    with selection_lock(root):
        old = selection_status(root)
        if old.get('id') == request_id:
            if old.get('voice_id') != voice_id:
                raise ValueError('同一任务标识不能更换目标声线')
            return old
        if old.get('phase') in PENDING and time.time() - (old.get('updated') or 0) < 300:
            raise ValueError('已有声线正在切换，请等待完成')
        engine_path = root / 'engine.json'
        before = json.loads(engine_path.read_text(encoding='utf-8')) if engine_path.exists() else {'backend': 'gpt-sovits'}
        if {**before, 'voice_id': before.get('voice_id', 'cyrene')} == entries[voice_id]['engine']:
            return {'id': request_id, 'voice_id': voice_id, 'phase': 'completed'}
        now = time.time()
        intent = read_control(root)
        record = {'id': request_id, 'voice_id': voice_id, 'phase': 'queued', 'error': '', 'at': now,
                  'updated': now, 'before': before, 'target': entries[voice_id]['engine'],
                  'desired_before': intent['desired'], 'control_before': intent.get('request_id')}
        write_json(root / 'voice-selection.json', record)
        python = Path('D:/anconda/envs/GPTSoVits/pythonw.exe')
        try:
            subprocess.Popen([str(python), '-X', 'utf8', '-I', str(Path(__file__).resolve()),
                              '--runtime', str(root), '--request-id', request_id],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
        except OSError:
            record.update(phase='failed', error='切换进程未启动，原模型未改动', updated=time.time())
            write_json(root / 'voice-selection.json', record)
            raise
        return selection_status(root)


def run_switch(root, request_id):
    import pc_voice_control as control
    root = Path(root)
    path = root / 'voice-selection.json'
    with selection_lock(root):
        record = json.loads(path.read_text(encoding='utf-8'))
        if record['id'] != request_id or record['phase'] != 'queued':
            return
        owned_control = None

        def update(phase, error=''):
            record.update(phase=phase, error=error, updated=time.time())
            write_json(path, record)

        def still_owned():
            if owned_control is not None and read_control(root).get('request_id') != owned_control:
                raise InterruptedError('启停状态被手动更改，已停止自动切换')

        def stop():
            nonlocal owned_control
            owned_control = control.action(root, 'stop')['request_id']
            deadline = time.monotonic() + 210
            while time.monotonic() < deadline:
                still_owned()
                status = control.snapshot(root)
                if status['phase'] == 'stopped' and not status['worker_running']:
                    state = read_worker(root)
                    try:
                        control.psutil.Process(state['pid']).wait(timeout=10)
                    except (control.psutil.NoSuchProcess, KeyError):
                        pass
                    return
                time.sleep(1)
            raise TimeoutError('当前语音未能及时结束，未强行切换')

        def start(voice_id):
            nonlocal owned_control
            owned_control = control.action(root, 'start')['request_id']
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                still_owned()
                status = control.snapshot(root)
                if status['model_ready'] and status.get('active_voice') == voice_id:
                    return status
                time.sleep(2)
            raise TimeoutError('新模型未能就绪')

        try:
            if read_control(root).get('request_id') != record.get('control_before'):
                raise InterruptedError('启停状态被手动更改，未启动自动切换')
            update('stopping')
            stop()
            still_owned()
            write_json(root / 'engine.json', record['target'])
            load_engine(root)  # Verify the selected package before launching.
            if record['desired_before']:
                update('loading')
                record['status'] = start(record['voice_id'])
            update('completed')
        except InterruptedError as exc:
            update('cancelled', str(exc))
        except Exception as exc:
            if owned_control is not None and read_control(root).get('request_id') != owned_control:
                update('cancelled', '启停状态被手动更改，已停止自动恢复')
                return
            if owned_control is not None:
                try:
                    update('rolling_back')
                    stop()
                    write_json(root / 'engine.json', record['before'])
                    if record['desired_before']:
                        update('loading')
                        start(record['before'].get('voice_id', 'cyrene'))
                except Exception:
                    update('failed', '切换未完成，恢复需要核对：' + type(exc).__name__)
                    return
            update('failed', '切换未完成，原模型已恢复：' + type(exc).__name__)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--request-id', required=True)
    args = parser.parse_args()
    run_switch(Path(args.runtime), args.request_id)
