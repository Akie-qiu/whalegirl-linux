"""Stage the expression preference delta over the current Linux release."""
import base64
import hashlib
import json
from pathlib import Path
import re
import tarfile
from stage_tool_reply_guard import remote,merge_delta

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/sticker-initiative'
FILES={'whalegirl_linux/'+n for n in ('settings.py','conversation.py','runtime.py','proactive.py','app.py','proactive.js','web.html')}|{'tests/test_sticker_preferences.py','tests/test_unified_actions.py'}


def main():
    live=json.loads(remote("import base64,json\nfrom pathlib import Path\nr=Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(sorted(FILES))+
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() for n in names}}))"))
    before={};after={}
    for name in sorted(FILES):
        raw=base64.b64decode(live['files'][name]);before[name]=hashlib.sha256(raw).hexdigest()
        saved=OUT/'server-before'/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(raw)
        if name.endswith('web.html'):
            value,count=re.subn(r'(/proactive\.js\?v=)[^"\s]+',r'\g<1>20261030-sticker-initiative',raw.decode())
            if count!=1:raise RuntimeError('Proactive script version anchor changed')
        else:
            value,_=merge_delta((OUT/'local-before'/name).read_text(encoding='utf-8'),
                (ROOT/name).read_text(encoding='utf-8'),raw.decode(),name)
        if name.endswith('.py'):compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(value,encoding='utf-8',newline='\n');after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':before,'after':after},indent=2))
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in sorted(FILES):archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'files':len(FILES),'root':live['root']}))


if __name__=='__main__':main()
