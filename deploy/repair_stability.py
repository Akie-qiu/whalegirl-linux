"""Repair the approved WeChat container, preserving its image, data and predecessor."""
import datetime
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import time

NAME = 'whalegirl-wechat-candidate-4118'
STAGE = Path(__file__).resolve().parent
BASE = Path('/opt/whalegirl-linux')
TOOLS = Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
OBSERVE = Path('/var/lib/whalegirl-linux/stability')
STAMP = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
BACKUP = STAGE / ('before-' + STAMP)


def run(args, timeout=40):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout).stdout.strip()


info = json.loads(run(['docker', 'inspect', NAME]))[0]
if not info['State']['Running'] or not info['Config']['Image'].startswith('whalegirl-wechat:4.1.1.8-'):
    raise SystemExit('Expected running pinned WeChat 4.1.1.8 container')
if run(['docker', 'exec', NAME, 'dpkg-query', '-W', '-f=${Version}', 'wechat']) != '4.1.1.8':
    raise SystemExit('Unexpected WeChat package; no change made')
if set(info['HostConfig']['PortBindings']) != {'6174/tcp'}:
    raise SystemExit('Unexpected ports; no change made')
if info['HostConfig']['NetworkMode'] not in ('default', 'bridge'):
    raise SystemExit('Unexpected network mode; no change made')
for filename in ['native_message_reader.py', 'start_native_reader.py', 'collect_stability.py']:
    compile((STAGE / filename).read_bytes(), filename, 'exec')
BACKUP.mkdir(mode=0o700)
(BACKUP / 'container-inspect.json').write_text(json.dumps(info, indent=2))
(BACKUP / 'container-inspect.json').chmod(0o600)
for path in [TOOLS / 'native_message_reader.py', BASE / 'start_native_reader.py', Path('/etc/fstab')]:
    if path.exists():
        shutil.copy2(path, BACKUP / path.name)

# This dedicated, initially absent file is the only target of allocation/mkswap.
swap = Path('/var/lib/whalegirl-linux/whalegirl.swap')
if swap.exists():
    raise SystemExit('Dedicated swap path already exists; preserved for inspection')
if shutil.disk_usage(swap.parent).free < 4 * 1024**3:
    raise SystemExit('Insufficient disk headroom; no swap created')
run(['fallocate', '-l', str(2 * 1024**3), str(swap)])
swap.chmod(0o600)
run(['mkswap', str(swap)])
run(['swapon', str(swap)])
with Path('/etc/fstab').open('a') as stream:
    stream.write('\n# WhaleGirl dedicated swap, ' + STAMP + '\n' + str(swap) + ' none swap sw 0 0\n')
run(['docker', 'update', '--memory', '1280m', '--memory-swap', '2560m', '--pids-limit', '1024', NAME])
print(json.dumps({'phase': 'resource_headroom_added', 'swap_gib': 2, 'memory_mib': 1280, 'memory_plus_swap_mib': 2560}), flush=True)

previous = NAME + '-before-stability-' + STAMP
run(['systemctl', 'stop', 'whalegirl-linux.service', 'whalegirl-native-reader.service'])
shutil.copy2(STAGE / 'native_message_reader.py', TOOLS / 'native_message_reader.py')
shutil.copy2(STAGE / 'start_native_reader.py', BASE / 'start_native_reader.py')
for path in [TOOLS / 'native_message_reader.py', BASE / 'start_native_reader.py']:
    path.chmod(0o644)
dropin = Path('/etc/systemd/system/whalegirl-native-reader.service.d/50-lifetime.conf')
dropin.parent.mkdir(exist_ok=True)
if dropin.exists():
    shutil.copy2(dropin, BACKUP / dropin.name)
dropin.write_text('[Service]\nExecStart=\nExecStart=/usr/bin/python3 /opt/whalegirl-linux/start_native_reader.py\nExecStop=/usr/bin/python3 /opt/whalegirl-linux/start_native_reader.py --stop\nTimeoutStopSec=15\n')
run(['docker', 'stop', '--time', '20', NAME])
run(['docker', 'rename', NAME, previous])

argv = ['docker', 'create', '--name', NAME, '--init', '--restart', 'unless-stopped',
        '--no-healthcheck', '--memory', '1280m', '--memory-swap', '2560m', '--cpus', '1',
        '--pids-limit', '1024', '--shm-size', str(info['HostConfig']['ShmSize']),
        '--hostname', info['Config']['Hostname']]
if info['HostConfig']['ReadonlyRootfs']:
    argv.append('--read-only')
for target, options in (info['HostConfig'].get('Tmpfs') or {}).items():
    argv += ['--tmpfs', target + ':' + options]
for option in info['HostConfig'].get('SecurityOpt') or []:
    argv += ['--security-opt', option]
for cap in info['HostConfig'].get('CapAdd') or []:
    argv += ['--cap-add', cap]
for cap in info['HostConfig'].get('CapDrop') or []:
    argv += ['--cap-drop', cap]
for mount in info['Mounts']:
    if mount['Type'] not in ('bind', 'volume'):
        continue
    source = mount['Name'] if mount['Type'] == 'volume' else mount['Source']
    option = 'type=' + mount['Type'] + ',src=' + source + ',dst=' + mount['Destination']
    if not mount['RW']:
        option += ',readonly'
    argv += ['--mount', option]
for container_port, bindings in info['HostConfig']['PortBindings'].items():
    for binding in bindings:
        if binding['HostIp'] != '127.0.0.1':
            raise RuntimeError('Unexpected exposed gateway binding')
        argv += ['--publish', binding['HostIp'] + ':' + binding['HostPort'] + ':' + container_port]
for key, value in (info['Config'].get('Labels') or {}).items():
    argv += ['--label', key + '=' + value]
argv += ['--label', 'com.whalegirl.stability-repair=' + STAMP]
for value in info['Config'].get('Env') or []:
    argv += ['--env', value]
if info['Config'].get('User'):
    argv += ['--user', info['Config']['User']]
if info['Config'].get('WorkingDir'):
    argv += ['--workdir', info['Config']['WorkingDir']]
entry = info['Config'].get('Entrypoint') or []
if isinstance(entry, str):
    entry = [entry]
if entry:
    argv += ['--entrypoint', entry[0]]
argv += [info['Image'], *entry[1:], *(info['Config'].get('Cmd') or [])]
try:
    run(argv)
    run(['docker', 'start', NAME])
except Exception:
    exists = subprocess.run(['docker', 'inspect', NAME], capture_output=True).returncode == 0
    if exists:
        run(['docker', 'stop', '--time', '10', NAME])
        run(['docker', 'rename', NAME, NAME + '-failed-' + STAMP])
    run(['docker', 'rename', previous, NAME])
    run(['docker', 'start', NAME])
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'start', 'whalegirl-native-reader.service', 'whalegirl-linux.service'])
    raise

OBSERVE.mkdir(mode=0o700, exist_ok=True)
projection = Path('/var/lib/whalegirl-linux/core/stability')
projection.mkdir(mode=0o750, exist_ok=True)
os.chown(projection, 0, pwd.getpwnam('whalegirl-ai').pw_gid)
projection.chmod(0o750)
shutil.copy2(STAGE / 'collect_stability.py', BASE / 'collect_stability.py')
Path('/etc/systemd/system/whalegirl-stability.service').write_text('''[Unit]
Description=Record WeChat login lifetime and resource failures
After=docker.service whalegirl-linux.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/whalegirl-linux/collect_stability.py
UMask=0077
Nice=10
MemoryMax=48M
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/whalegirl-linux/stability /var/lib/whalegirl-linux/core/stability
''')
Path('/etc/systemd/system/whalegirl-stability.timer').write_text('''[Unit]
Description=Collect WeChat stability once per minute
[Timer]
OnBootSec=1min
OnUnitActiveSec=60s
AccuracySec=5s
[Install]
WantedBy=timers.target
''')
run(['systemctl', 'daemon-reload'])
run(['systemctl', 'start', 'whalegirl-native-reader.service', 'whalegirl-linux.service'])
run(['systemctl', 'enable', '--now', 'whalegirl-stability.timer'])
report = {'at': time.time(), 'container': NAME, 'previous_container': previous,
          'image_id': info['Image'], 'wechat_version': '4.1.1.8', 'init': True,
          'memory_mib': 1280, 'memory_plus_swap_mib': 2560, 'pids_limit': 1024,
          'added_host_swap_gib': 2, 'backup': str(BACKUP), 'observation_path': str(OBSERVE)}
(OBSERVE / 'deployment.json').write_text(json.dumps(report, indent=2))
(STAGE / 'deployed.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report), flush=True)
