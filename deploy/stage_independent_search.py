"""Merge only independent-search deltas into the running release."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
from stage_tool_reply_guard import remote,merge_delta

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/search-independent'
BASELINE='fc36d97'
NEW={'tests/test_independent_search.py'}
FILES={'whalegirl_linux/'+name for name in ('search.py','settings.py','runtime.py','app.py','web.html','habits.js','console.js')}|NEW|{'tests/test_search.py'}


def main():
    live=json.loads(remote("import base64,json\nfrom pathlib import Path\nr=Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(sorted(FILES))+
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))"))
    before={};after={};anchors={}
    for name in sorted(FILES):
        encoded=live['files'][name]
        raw=base64.b64decode(encoded) if encoded is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        if name in NEW:
            if raw is not None:raise RuntimeError('New search source already exists: '+name)
            value=(ROOT/name).read_text(encoding='utf-8')
        else:
            if raw is None:raise RuntimeError('Missing live source: '+name)
            saved=OUT/'server-before'/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(raw)
            previous=subprocess.check_output(['git','show',BASELINE+':'+name],cwd=ROOT).decode()
            value,anchors[name]=merge_delta(previous,(ROOT/name).read_text(encoding='utf-8'),raw.decode(),name)
        if name.endswith('.py'):compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(value,encoding='utf-8',newline='\n')
        after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':before,'after':after,'unique_line_anchors':anchors},indent=2))
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in sorted(FILES):archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'files':len(FILES),'root':live['root'],'unique_line_anchors':anchors}))


if __name__=='__main__':main()
