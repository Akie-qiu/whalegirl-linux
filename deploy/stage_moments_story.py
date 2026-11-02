"""Apply only this story-creation delta to the actual production release."""
import base64
import hashlib
import json
from pathlib import Path
import tarfile

from stage_tool_reply_guard import merge_delta, remote

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/moments-story-20261030'
EXISTING = {'whalegirl_linux/moments.py', 'whalegirl_linux/images.py',
            'whalegirl_linux/image_references.py', 'MOMENTS.md'}
NEW = {'whalegirl_linux/moments_creativity.py', 'tests/test_moments_story.py',
       'MOMENTS_STORY_PLAN_20261030.md'}
FILES = EXISTING | NEW


def main():
    live = json.loads(remote("import pathlib,base64,json\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nnames="
        + repr(sorted(FILES)) + "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))"))
    previous_manifest = OUT / 'manifest.json'
    previous = json.loads(previous_manifest.read_text(encoding='utf-8')) if previous_manifest.exists() else {}
    followup = live['root'] != previous.get('root') and all(
        live['files'].get(name) is not None and hashlib.sha256(base64.b64decode(live['files'][name])).hexdigest()
        == previous.get('after', {}).get(name) for name in FILES)
    before, after, reviewed = {}, {}, {}
    for name in sorted(FILES):
        raw = base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name] = hashlib.sha256(raw).hexdigest() if raw is not None else None
        if followup:
            saved = OUT / 'server-followup-before' / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(raw)
            value, reviewed[name] = merge_delta((OUT / 'payload' / name).read_text(encoding='utf-8'),
                (ROOT / name).read_text(encoding='utf-8'), raw.decode('utf-8'), name)
        elif name in NEW:
            if raw is not None:
                raise RuntimeError('New file already exists in production: ' + name)
            value = (ROOT / name).read_text(encoding='utf-8')
        else:
            saved = OUT / 'server-before' / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(raw)
            value, reviewed[name] = merge_delta((OUT / 'local-before' / name).read_text(encoding='utf-8'),
                (ROOT / name).read_text(encoding='utf-8'), raw.decode('utf-8'), name)
        if name.endswith('.py'):
            compile(value, name, 'exec')
        target = OUT / 'payload' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding='utf-8', newline='\n')
        after[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT / 'manifest.json').write_text(json.dumps({'root': live['root'], 'before': before,
        'after': after, 'unique_line_anchors': reviewed}, indent=2), encoding='utf-8')
    with tarfile.open(OUT / 'release.tar', 'w') as archive:
        for name in sorted(FILES):
            archive.add(OUT / 'payload' / name, arcname=name)
    print(json.dumps({'root': live['root'], 'files': len(FILES), 'unique_line_anchors': reviewed}))


if __name__ == '__main__':
    main()
