"""Merge canvas/4K changes only; do not ship unrelated local work."""
import base64
import hashlib
import json
from pathlib import Path
import tarfile
from stage_tool_reply_guard import merge_delta, remote

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/image-4k-ratio-20261030'
EXISTING = {'whalegirl_linux/images.py','whalegirl_linux/image_tools.py','whalegirl_linux/image_references.py',
    'whalegirl_linux/moments.py','whalegirl_linux/images.js','whalegirl_linux/app.py','whalegirl_linux/web.html','MOMENTS.md'}
NEW = {'whalegirl_linux/image_canvas.py','tests/test_image_canvas.py','tests/test_image_tools.py','IMAGE_4K_RATIO_PLAN_20261030.md'}
FILES = EXISTING | NEW


def main():
    live = json.loads(remote("import pathlib,base64,json\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nnames="
        + repr(sorted(FILES)) + "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))"))
    before, after, reviewed = {}, {}, {}
    for name in sorted(FILES):
        raw = base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name] = hashlib.sha256(raw).hexdigest() if raw is not None else None
        if name in NEW:
            if raw is not None:
                raise RuntimeError('New canvas file already exists: ' + name)
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
    (OUT / 'manifest.json').write_text(json.dumps({'root': live['root'],'before': before,
        'after': after,'unique_line_anchors': reviewed}, indent=2), encoding='utf-8')
    with tarfile.open(OUT / 'release.tar', 'w') as archive:
        for name in sorted(FILES):
            archive.add(OUT / 'payload' / name, arcname=name)
    print(json.dumps({'root': live['root'],'files':len(FILES),'unique_line_anchors':reviewed}))


if __name__ == '__main__':
    main()
