"""Guarded local activation/rollback; no server credentials or WeChat actions."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pc_voice_control as control
from pc_voice_common import read_control, read_worker, write_json
from pc_voice_engine import load_engine

ROOT = control.ROOT
RECORD = ROOT.parent / 'aniflive-trial/integration-activation.json'
CANDIDATE = {'backend': 'aniflive', 'package': 'cyrene-win-opt0',
             'fingerprint': 'bcad74142564b6a9f8d8f32f'}
LAST_START_REQUEST_ID = None
LAST_STOP_REQUEST_ID = None


def stop():
    global LAST_STOP_REQUEST_ID
    control.action(ROOT, 'stop')
    request = read_control(ROOT)
    LAST_STOP_REQUEST_ID = request['request_id']
    until = time.monotonic() + 220
    while time.monotonic() < until:
        if read_control(ROOT).get('request_id') != request['request_id']:
            raise RuntimeError('Owner changed the switch; activation stopped')
        status = control.snapshot(ROOT)
        if status['phase'] == 'stopped' and not status['worker_running']:
            worker = read_worker(ROOT)
            try:
                process = control.psutil.Process(worker['pid'])
                process.wait(timeout=10)
            except control.psutil.NoSuchProcess:
                pass
            return request
        time.sleep(1)
    raise TimeoutError('Voice did not drain; configuration was not changed')


def start(expected_backend):
    global LAST_START_REQUEST_ID
    control.action(ROOT, 'start')
    request = read_control(ROOT)
    LAST_START_REQUEST_ID = request['request_id']
    until = time.monotonic() + 150
    while time.monotonic() < until:
        if read_control(ROOT).get('request_id') != request['request_id']:
            raise RuntimeError('Owner changed the switch; do not override')
        status = control.snapshot(ROOT)
        if status['model_ready'] and status['cloud_confirmed'] and status.get('engine') == expected_backend:
            return status
        time.sleep(2)
    raise TimeoutError('Voice activation did not become ready')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['activate', 'rollback', 'status'])
    action = parser.parse_args().action
    if action == 'status':
        print(json.dumps(control.snapshot(ROOT)), flush=True)
        return
    if action == 'activate':
        assert not RECORD.exists(), 'Activation already recorded; inspect instead of repeating'
        path = ROOT / 'engine.json'
        before = json.loads(path.read_text()) if path.exists() else {'backend': 'gpt-sovits'}
        previous_control = read_control(ROOT)
        assert previous_control['desired'], 'Owner had stopped voice; do not enable it implicitly'
        record = {'before_engine': before, 'before_control': previous_control,
                  'candidate': CANDIDATE, 'phase': 'draining', 'at': time.time()}
        write_json(RECORD, record)
        stop_request = stop()
        record.update(phase='stopped', stop_request_id=stop_request['request_id'])
        write_json(RECORD, record)
        write_json(path, CANDIDATE)
        try:
            load_engine(ROOT)
            status = start('aniflive')
        except Exception:
            # Preserve an explicit later owner choice, including a manual stop.
            now = read_control(ROOT)
            expected_id = LAST_START_REQUEST_ID or stop_request['request_id']
            if now.get('request_id') == expected_id:
                if now.get('desired'):
                    stop()
                write_json(path, before)
                status = start(before['backend'])
                record.update(phase='activation_failed_legacy_restored', status=status)
                write_json(RECORD, record)
            raise
        record.update(phase='active', activated_at=time.time(), status=status)
        write_json(RECORD, record)
    else:
        record = json.loads(RECORD.read_text())
        assert json.loads((ROOT / 'engine.json').read_text()) == record['candidate'], 'Engine changed elsewhere'
        was_enabled = read_control(ROOT)['desired']
        stop()
        write_json(ROOT / 'engine.json', record['before_engine'])
        status = start(record['before_engine']['backend']) if was_enabled else control.snapshot(ROOT)
        record.update(phase='rolled_back', rolled_back_at=time.time(), status=status)
        write_json(RECORD, record)
    print(json.dumps({'phase': record['phase'], 'status': status}), flush=True)


if __name__ == '__main__':
    main()
