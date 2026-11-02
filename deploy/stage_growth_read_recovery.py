"""Thin follow-up release after a real relay timeout; reuse saved reading sources."""
import base64
import hashlib
import json
from pathlib import Path
import tarfile
from stage_tool_reply_guard import remote,merge_delta

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/growth-read-recovery'
FILES={'whalegirl_linux/growth.py','tests/test_autonomous_growth.py'}


def main():
    live=json.loads(remote("import base64,json\nfrom pathlib import Path\nr=Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(sorted(FILES))+
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() for n in names}}))"))
    before={};after={}
    for name in sorted(FILES):
        raw=base64.b64decode(live['files'][name]);before[name]=hashlib.sha256(raw).hexdigest()
        value,_=merge_delta((OUT/'local-before'/name).read_text(encoding='utf-8'),
            (ROOT/name).read_text(encoding='utf-8'),raw.decode(),name)
        compile(value,name,'exec');target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(value,encoding='utf-8',newline='\n');after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':before,'after':after},indent=2))
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in sorted(FILES):archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'files':len(FILES),'root':live['root']}))


if __name__=='__main__':main()
