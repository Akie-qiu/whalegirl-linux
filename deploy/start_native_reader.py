"""Start the read-only live-message service without restarting native WeChat."""
import json
import os
from pathlib import Path
import subprocess
import signal
import sys
import time


NAME = 'whalegirl-wechat-candidate-4118'
info = json.loads(subprocess.check_output(['docker', 'inspect', NAME], text=True))[0]


def readers():
    found = []
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            if info['Id'] not in (process / 'cgroup').read_text():
                continue
            argv = (process / 'cmdline').read_bytes().split(b'\0')
            if argv[:3] == [b'python3', b'-u', b'/data/native-tools/native_message_reader.py']:
                found.append((int(process.name), (process / 'stat').read_text().rsplit(') ', 1)[1].split()[19]))
        except (FileNotFoundError, ProcessLookupError):
            pass
    return found


def stop_readers():
    selected = readers()
    # Stop only the exact native-reader commands in this container. Let the new
    # reader close its own SQLCipher children; legacy instances are handled too.
    for pid, started in selected:
        try:
            process = Path('/proc') / str(pid)
            if (process / 'stat').read_text().rsplit(') ', 1)[1].split()[19] == started:
                os.kill(pid, signal.SIGTERM)
        except (FileNotFoundError, ProcessLookupError):
            pass
    deadline = time.monotonic() + 5
    while readers() and time.monotonic() < deadline:
        time.sleep(.1)
    if readers():
        raise RuntimeError('Native reader did not stop; refusing duplicate listener')
    print(json.dumps({'native_readers_stopped': len(selected)}), flush=True)


if sys.argv[1:] == ['--stop']:
    stop_readers()
    raise SystemExit(0)
if sys.argv[1:]:
    raise SystemExit('Unsupported native reader command')
image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', info['Image']], text=True))[0]
pinned_image = any(tag.startswith('whalegirl-wechat:4.1.1.8-') for tag in image.get('RepoTags') or [])
if not info['State']['Running'] or not pinned_image:
    raise SystemExit('The pinned native WeChat container is not running')
version = Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/runtime-version.json')
if json.loads(version.read_text())['wechat_version'] != '4.1.1.8':
    raise SystemExit('Unexpected native WeChat version')
# Reapply the AT-SPI list traversal repair after a container or host restart.
# The pinned image remains read-only; this binds the backed-up helper only.
a11y = Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools/a11y_safe.py')
if a11y.is_file():
    subprocess.run(['nsenter', '--target', str(info['State']['Pid']), '--mount', '--', 'mount', '--bind',
                    '/data/native-tools/a11y_safe.py', '/opt/tools/a11y-dump'], check=True, timeout=10)
stop_readers()
subprocess.run(['nsenter', '--target', str(info['State']['Pid']), '--mount', '--', 'mount', '--bind',
                '/data/native-tools/chat_select_linux.py', '/opt/tools/chat-select.py'], check=True, timeout=10)
os.execvp('docker', ['docker', 'exec', NAME, 'python3', '-u', '/data/native-tools/native_message_reader.py'])
