"""Stage autonomous tools against the running release, preserving its other fixes."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
from stage_tool_reply_guard import merge_delta, remote

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/proactive-tools-merge'
BASELINE='f86bc6e'
NEW={'whalegirl_linux/proactive_tools.py','tests/test_proactive_tools.py'}
FILES=NEW|{'whalegirl_linux/proactive.py','whalegirl_linux/tieba_tools.py'}


def main():
    live=json.loads(remote("import pathlib,base64,json\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(sorted(FILES))+
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))"))
    before={};after={};anchors={}
    for name in sorted(FILES):
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        updated=(ROOT/name).read_text(encoding='utf-8')
        if name in NEW:
            if raw is not None:raise RuntimeError('New file already exists: '+name)
            value=updated
        else:
            previous=subprocess.check_output(['git','show',BASELINE+':'+name],cwd=ROOT).decode('utf-8')
            for folder,data in [('server-before',raw),('local-before',previous.encode())]:
                saved=OUT/folder/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(data)
            value,anchors[name]=merge_delta(previous,updated,raw.decode('utf-8'),name)
        compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(value,encoding='utf-8',newline='\n');after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':before,'after':after,'unique_line_anchors':anchors},indent=2))
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in sorted(FILES):archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'files':len(FILES),'root':live['root'],'unique_line_anchors':anchors}))


if __name__=='__main__':main()
