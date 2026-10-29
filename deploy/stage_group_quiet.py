"""Merge group quiet/wake behavior into the active release without other changes."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
from stage_tool_reply_guard import remote,merge_delta

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/group-quiet'
BASELINE='02e996b'
FILES={'whalegirl_linux/chat_control.py','whalegirl_linux/runtime.py','whalegirl_linux/proactive.js',
       'whalegirl_linux/web.html','tests/test_chat_control.py','tests/test_silence_commands.py'}


def main():
    live=json.loads(remote("import base64,json\nfrom pathlib import Path\nr=Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(sorted(FILES))+
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() for n in names}}))"))
    before={};after={};anchors={}
    for name in sorted(FILES):
        raw=base64.b64decode(live['files'][name]);before[name]=hashlib.sha256(raw).hexdigest()
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
