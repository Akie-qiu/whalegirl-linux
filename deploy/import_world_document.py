"""Apply the supplied V3 through the same authenticated API as the editor."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import urllib.request

DATA = Path('/var/lib/whalegirl-linux/core')
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(route, body=None):
    request = urllib.request.Request('http://127.0.0.1:8092/api/' + route,
        headers={'Authorization': 'Bearer ' + (DATA / 'admin.token').read_text().strip(), 'Content-Type': 'application/json'},
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None)
    with OPENER.open(request, timeout=110) as response:
        return json.load(response)


def references(account):
    path = DATA / 'moments-world' / hashlib.sha256(account.encode()).hexdigest()[:20] / 'cast.json'
    return {v['id']: v['reference_id'] for v in json.loads(path.read_text())['characters']}


def events():
    with sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True) as db:
        return db.execute('SELECT count(*) FROM moments_world_events').fetchone()[0]


def main():
    source = Path(sys.argv[1])
    target = source.parent / 'import-result.json'
    if target.exists():
        raise RuntimeError('Import was already recorded; do not recompile it')
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != '55fdfbde1b1feb837756d07e9cfdb98c73126b8faca4c33717ae15ae04057f63':
        raise RuntimeError('The supplied V3 file checksum changed')
    content = raw.decode('utf-8')
    before = api('moments/world')
    document = api('moments/world/document')
    account = api('state')['account_id']
    refs, event_count = references(account), events()
    started = time.monotonic()
    result = api('moments/world', {'enabled': before['enabled'], 'setting_text': content,
        'expected_setting_revision': document['revision']})
    saved = api('moments/world/document')
    characters = {c['id']: c for c in result['characters']}
    relationship = next(r for r in result['design']['relationships'] if {r['a'], r['b']} == {'whalegirl', 'claude'})
    report = {'revision': saved['revision'], 'source_characters': len(content), 'title': saved['title'],
        'full_source_preserved': saved['text'] == content, 'reference_ids_preserved': references(account) == refs,
        'event_history_preserved': events() >= event_count, 'world_version': result['version'],
        'claude_personality': characters['claude']['personality'],
        'whalegirl_personality': characters['whalegirl']['personality'], 'main_relationship': relationship,
        'seconds': round(time.monotonic() - started, 3), 'image_requests_submitted': 0, 'native_messages_sent': 0}
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    target.chmod(0o600)
    print(json.dumps(report, ensure_ascii=False))
    if not all(report[k] for k in ('full_source_preserved', 'reference_ids_preserved', 'event_history_preserved')):
        raise RuntimeError('World import preservation check failed')


if __name__ == '__main__':
    main()
