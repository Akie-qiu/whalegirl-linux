"""Merge only this repair into the running release; retain all backups."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

from stage_tool_reply_guard import merge_delta, remote

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/search-clock-merge'
BASELINE = '1744567'
NEW = {'whalegirl_linux/time_context.py', 'tests/test_search_clock.py'}
FILES = NEW | {'whalegirl_linux/' + name for name in (
    'runtime.py', 'proactive.py', 'search.py', 'context.py', 'upstream/ai_engine.py')}
FILES |= {'tests/test_message_time_context.py', 'tests/test_responses.py'}


def main():
    code = ("import pathlib,base64,json\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nnames="
            + repr(sorted(FILES))
            + "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))")
    live = json.loads(remote(code))
    before, after, anchors = {}, {}, {}
    for name in sorted(FILES):
        raw = base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name] = hashlib.sha256(raw).hexdigest() if raw is not None else None
        updated = (ROOT / name).read_text(encoding='utf-8')
        if raw is None and name.startswith('tests/'):
            value = updated
        elif name in NEW:
            if raw is not None:
                raise RuntimeError('New file already exists: ' + name)
            value = updated
        else:
            previous = subprocess.check_output(['git', 'show', BASELINE + ':' + name], cwd=ROOT).decode('utf-8')
            for folder, data in (('server-before', raw), ('local-before', previous.encode())):
                saved = OUT / folder / name
                saved.parent.mkdir(parents=True, exist_ok=True)
                saved.write_bytes(data)
            production = raw.decode('utf-8')
            if name == 'whalegirl_linux/proactive.py':
                old = ("            raw = await asyncio.wait_for(engine.chat(tool_messages,\n"
                       "                system_prompt=system, max_tokens=s.max_tokens+400, response_format={'type':'json_object'}), timeout=15)\n")
                expected = ("            raw = await asyncio.wait_for(engine.chat(tool_messages,\n"
                            "                system_prompt=system, max_tokens=s.max_tokens+400, response_format={'type':'json_object'},\n"
                            "                operation='proactive'), timeout=15)\n")
                if old in production:
                    if production.count(old) != 1:
                        raise RuntimeError('Ambiguous proactive request anchor')
                    production = production.replace(old, expected, 1)
            value, anchors[name] = merge_delta(previous, updated, production, name)
        compile(value, name, 'exec')
        target = OUT / 'payload' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding='utf-8', newline='\n')
        after[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT / 'manifest.json').write_text(json.dumps({
        'root':live['root'], 'before':before, 'after':after, 'unique_line_anchors':anchors}, indent=2))
    with tarfile.open(OUT / 'release.tar', 'w') as archive:
        for name in sorted(FILES):
            archive.add(OUT / 'payload' / name, arcname=name)
    print(json.dumps({'files':len(FILES), 'root':live['root'], 'unique_line_anchors':anchors}))


if __name__ == '__main__':
    main()
