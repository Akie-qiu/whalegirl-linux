"""Stage only manual sticker preferences and the editable curated reference."""
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
from stage_tool_reply_guard import remote,merge_delta

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/sticker-preferences-merge'
BASELINE='096ff8d'
NEW={'tests/test_sticker_preferences.py'}
FILES=NEW|{'whalegirl_linux/'+n for n in ('settings.py','conversation.py','app.py','proactive.js','web.html')}


def main():
    live=json.loads(remote("import pathlib,base64,json\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(sorted(FILES))+
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))"))
    before={};after={}
    for name in sorted(FILES):
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        value=(ROOT/name).read_text(encoding='utf-8')
        if name in NEW:
            if raw is not None:raise RuntimeError('New test already exists')
        else:
            if raw is None:raise RuntimeError('Missing live source: '+name)
            saved=OUT/'server-before'/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(raw)
            previous=subprocess.check_output(['git','show',BASELINE+':'+name],cwd=ROOT).decode()
            if name.endswith('web.html'):
                value,count=re.subn(r'(/proactive\.js\?v=)[^"\s]+',r'\g<1>20261029-sticker-preferences',raw.decode())
                if count!=1:raise RuntimeError('Changed proactive asset tag')
            else:value,_=merge_delta(previous,value,raw.decode(),name)
        if name.endswith('.py'):compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(value,encoding='utf-8',newline='\n')
        after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':before,'after':after},indent=2))
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in sorted(FILES):archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'files':len(FILES),'root':live['root']}))


if __name__=='__main__':main()
