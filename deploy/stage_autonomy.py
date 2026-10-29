"""Apply only the autonomous-chat delta to the actual live release."""
import base64
import hashlib
import json
import re
from pathlib import Path
import subprocess
import tarfile

from stage_tool_reply_guard import merge_delta, remote

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'artifacts/autonomy-upgrade'
BASELINE = '0a70248'
EXISTING = ['whalegirl_linux/'+name for name in (
    'app.py','conversation.py','habits.js','proactive.py','runtime.py','settings.py','web.html')]
NEW = ['whalegirl_linux/autonomy.py','tests/test_autonomy.py','tests/test_proactive.py']
FILES = EXISTING + NEW


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    code = "import pathlib,base64,json\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nnames=" + repr(FILES)
    code += "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))"
    live = json.loads(remote(code))
    before, after, anchors = {}, {}, {}
    for name in FILES:
        raw = base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name] = hashlib.sha256(raw).hexdigest() if raw is not None else None
        updated = (ROOT/name).read_text(encoding='utf-8')
        if name in NEW:
            if raw is not None:
                raise RuntimeError('New file already exists on active release: '+name)
            value = updated
        else:
            saved = OUT/'server-before'/name
            saved.parent.mkdir(parents=True,exist_ok=True)
            saved.write_bytes(raw)
            previous = subprocess.check_output(['git','show',BASELINE+':'+name],cwd=ROOT).decode('utf-8')
            saved_local = OUT/'local-before'/name
            saved_local.parent.mkdir(parents=True,exist_ok=True)
            saved_local.write_text(previous,encoding='utf-8')
            production = raw.decode('utf-8')
            if name == 'whalegirl_linux/web.html':
                # Other live script cache tags differ from the local snapshot.
                # Merge only this panel and change this script's own cache tag.
                updated = updated.replace('/habits.js?v=20261028-autonomy-switch',
                                          '/habits.js?v=20261028-followup')
            value, anchors[name] = merge_delta(previous,updated,production,name)
            if name == 'whalegirl_linux/web.html':
                value, count = re.subn(r'/habits\.js\?v=[^"\s]+',
                                      '/habits.js?v=20261028-autonomy-switch',value)
                if count != 1:
                    raise RuntimeError('Expected one habits script in live HTML')
        if name.endswith('.py'):
            compile(value,name,'exec')
        target = OUT/'payload'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(value,encoding='utf-8',newline='\n')
        after[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = {'root':live['root'],'baseline':BASELINE,'before':before,'after':after,'unique_line_anchors':anchors}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in FILES:
            archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'staged':len(FILES),'root':live['root'],'unique_line_anchors':anchors}))


if __name__ == '__main__':
    main()
