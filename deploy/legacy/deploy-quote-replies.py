"""Deploy optional native quoting without restarting the logged-in WeChat client."""
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

ROOT = Path('/opt/whalegirl-linux/current')
DATA = Path('/var/lib/whalegirl-linux/core')
ASSETS = Path('/opt/whalegirl-linux-assets/candidate-4.1.1.8')
TOOLS = Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
TOKEN = (DATA / 'admin.token').read_text().strip()


def api(route, body=None):
    request = urllib.request.Request('http://127.0.0.1:8092/api/' + route,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(request, timeout=35))


def service(*args):
    subprocess.run(['systemctl', *args], check=True, timeout=40)


original = api('config')
original.pop('api_key_configured', None)
before = api('state')
backup = ASSETS / ('quote-replies-before-' + str(time.time_ns()))
backup.mkdir(mode=0o700)
shutil.copy2(DATA / 'settings.json', backup / 'settings.json')
names = {'whalegirl_linux/settings.py', 'whalegirl_linux/runtime.py',
    'whalegirl_linux/gateway.py', 'whalegirl_linux/app.py',
    'whalegirl_linux/console.js', 'whalegirl_linux/web.html',
    'deploy/chat_select_linux.py', 'deploy/native_message_reader.py'}
with tarfile.open(ASSETS / 'quote-replies-20261020.tar') as archive:
    members = archive.getmembers()
    assert {m.name for m in members} == names and all(m.isfile() for m in members)
    staged = {m.name: archive.extractfile(m).read() for m in members}
for name, source in staged.items():
    if name.endswith('.py'):
        compile(source, name, 'exec')
    saved = backup / name
    saved.parent.mkdir(parents=True, exist_ok=True)
    if (ROOT / name).exists():
        shutil.copy2(ROOT / name, saved)
for name in ['chat_select_linux.py', 'native_message_reader.py']:
    shutil.copy2(TOOLS / name, backup / ('live-' + name))
api('config', {'settings': {**original, 'enabled': False}})
for _ in range(100):
    snapshot = api('state')
    if not snapshot['processing'] and not any(snapshot['counts'].get(k) for k in ('generating', 'sending')):
        break
    time.sleep(.5)
else:
    api('config', {'settings': original})
    raise RuntimeError('Core worker is still sending; deployment deferred')
service('stop', 'whalegirl-linux.service')
try:
    for name, source in staged.items():
        (ROOT / name).write_bytes(source)
    # Keep the inodes mounted into the already-running native client container.
    for name in ['chat_select_linux.py', 'native_message_reader.py']:
        (TOOLS / name).write_bytes(staged['deploy/' + name])
finally:
    service('start', 'whalegirl-linux.service')
for _ in range(40):
    try:
        api('config')
        break
    except OSError:
        time.sleep(.5)
else:
    raise RuntimeError('Updated core did not start')
api('config', {'settings': {**original, 'enabled': False, 'quote_replies_enabled': True}})
service('restart', 'whalegirl-native-reader.service')
sys.path.insert(0, str(ROOT))
from whalegirl_linux.gateway import AgentGateway


async def accept():
    gateway = AgentGateway('http://127.0.0.1:6175', (DATA / 'gateway.token').read_text().strip(), control_directory=DATA)
    gateway.ui_jobs_directory = TOOLS.parent / 'whalegirl-jobs'
    chat = '56614122712@chatroom'
    try:
        rows = await gateway.messages(chat, limit=100)
        target = max((m for m in rows if m.get('type') == 1 and m.get('isSelf') is False),
                     key=lambda m: (m['timestamp'], m['localId']))
        quote = {'local_id': target['localId'], 'server_id': str(target['serverId']), 'content': target['content']}
        probe = await gateway.ui_job('inspect_quote_menu', chat, quote_to=quote)
        print(json.dumps({'native_menu': probe.get('menu'), 'quote_error': probe.get('quote_unavailable')}, ensure_ascii=False), flush=True)
        if not probe.get('menu'):
            raise RuntimeError('Native quote menu was not available')
        started = time.monotonic()
        receipt = await gateway.send(chat, '引用回复测试：现在可以对应到这条消息。', quote_to=quote)
        result = {'confirmed': receipt.confirmed, 'quoted': receipt.quoted, 'error': receipt.error,
                  'quote_error': receipt.quote_error, 'target_local_id': target['localId'],
                  'target_server_id': quote['server_id'], 'outgoing_message_id': receipt.message_id,
                  'send_readback_seconds': round(time.monotonic() - started, 3)}
        print(json.dumps({'acceptance': result}, ensure_ascii=False), flush=True)
        assert receipt.confirmed and receipt.quoted, 'Native quote was not read back against the exact source ID'
        return result
    finally:
        await gateway.close()


accepted = False
try:
    result = asyncio.run(accept())
    accepted = True
finally:
    api('config', {'settings': {**original, 'quote_replies_enabled': accepted}})
final = api('state')
report = {'backup': str(backup), 'native_quote': result, 'enabled': final['enabled'],
          'quote_replies_enabled': api('config')['quote_replies_enabled'],
          'paused_reason': final['paused_reason'], 'existing_uncertain_before': before['counts'].get('uncertain', 0),
          'uncertain_after': final['counts'].get('uncertain', 0)}
(ASSETS / 'quote-replies-acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False), flush=True)
