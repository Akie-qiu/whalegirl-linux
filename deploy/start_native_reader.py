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
def install_helper(name, destination):
    # Resolve the actual inode inside the running container. Atomic host updates
    # can leave an old single-file bind in place. Do not stack another mount on
    # each reader restart when the helper is already current.
    source = '/data/native-tools/' + name
    check = "import pathlib,sys;sys.exit(0 if pathlib.Path(sys.argv[1]).read_bytes()==pathlib.Path(sys.argv[2]).read_bytes() else 1)"
    args = ['docker', 'exec', NAME, 'python3', '-c', check, source, destination]
    if subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8).returncode == 0:
        return
    copy = "import pathlib,sys;pathlib.Path(sys.argv[2]).write_bytes(pathlib.Path(sys.argv[1]).read_bytes())"
    result = subprocess.run(['docker', 'exec', NAME, 'python3', '-c', copy, source, destination],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
    if result.returncode:
        # A newly created pinned container has a read-only root. Enter its root
        # as well as its mount namespace so both paths resolve in the container.
        subprocess.run(['nsenter', '--target', str(info['State']['Pid']), '--mount', '--root', '--wd', '--',
                        'mount', '--bind', source, destination], check=True, timeout=10)
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, timeout=8)


def install_health_guard():
    # /usr/local/sbin precedes /usr/bin in the running gateway's PATH.
    # Preserve all existing commands: this deployment requires an empty sbin
    # directory (or our exact previous guard), never masks unrelated binaries.
    source = '/data/native-tools/health-guard-bin'
    check = '''from pathlib import Path
import sys
root=Path('/usr/local/sbin')
names={p.name for p in root.iterdir()}
if names-{'kill'}:sys.exit(2)
target=root/'kill'
if target.exists():
 sys.exit(0 if target.read_bytes()==Path('/data/native-tools/health_restart_guard.py').read_bytes() else 2)
sys.exit(1)
'''
    result = subprocess.run(['docker','exec',NAME,'python3','-c',check],
                            capture_output=True,text=True,timeout=8)
    if result.returncode == 0:
        return
    if result.returncode != 1:
        raise RuntimeError('Health guard PATH contains other files; preserved')
    subprocess.run(['nsenter','--target',str(info['State']['Pid']),'--mount','--root','--wd','--',
                    'mount','--bind',source,'/usr/local/sbin'],check=True,timeout=10)
    subprocess.run(['docker','exec',NAME,'python3','-c',check],check=True,timeout=8)


if Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools/health-guard-bin/kill').is_file():
    install_health_guard()
stop_readers()
if Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools/a11y_safe.py').is_file():
    install_helper('a11y_safe.py', '/opt/tools/a11y-dump')
install_helper('chat_select_linux.py', '/opt/tools/chat-select.py')
os.execvp('docker', ['docker', 'exec', NAME, 'python3', '-u', '/data/native-tools/native_message_reader.py'])
