"""Small local lifecycle state; no credentials, platform writes or model imports."""
from pathlib import Path
import json
import os
import time
import uuid


class ModelStartupGuard:
    """Bound consecutive model exits until a fresh owner start or readiness."""
    def __init__(self, request_id):
        self.request_id = request_id
        self.failures = 0
        self.exit_code = None

    def control(self, request_id):
        if request_id != self.request_id:
            self.request_id = request_id
            self.ready()

    def exited(self, exit_code=None):
        self.failures += 1
        self.exit_code = exit_code

    def ready(self):
        self.failures = 0
        self.exit_code = None

    @property
    def blocked(self):
        return self.failures >= 3

    @property
    def error(self):
        if not self.blocked:
            return ''
        return '模型连续启动失败 3 次，已暂停重试；请检查后点击启动语音'


def write_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    for attempt in range(21):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            # Windows readers/scanners can briefly hold the destination without
            # FILE_SHARE_DELETE. Keep the last complete state until replacement.
            if attempt == 20:
                raise
            time.sleep(.05)


def read_control(root):
    path = Path(root) / 'service-control.json'
    if not path.exists():
        # Compatibility for an already-running bridge created before this UI.
        return {'desired': True, 'requested_at': 0}
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if type(value.get('desired')) is not bool:
            raise ValueError()
        return value
    except (OSError, ValueError, TypeError):
        return {'desired': False, 'requested_at': time.time(), 'reason': 'invalid_control_state'}


def request_control(root, desired):
    value = {'desired': bool(desired), 'requested_at': time.time(), 'request_id': uuid.uuid4().hex}
    write_json(Path(root) / 'service-control.json', value)
    return value


def read_worker(root):
    try:
        return json.loads((Path(root) / 'worker-status.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
