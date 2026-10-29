"""Stage per-chat quiet and consolidated UI without replacing unrelated live fixes."""
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
from stage_tool_reply_guard import remote,merge_delta

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/chat-quiet-merge'
BASELINE='3a50abc'
NEW={'whalegirl_linux/chat_control.py','tests/test_chat_control.py'}
FILES=NEW|{'whalegirl_linux/'+n for n in ('runtime.py','settings.py','proactive.py','images.py','tieba.py','app.py',
                                         'console.js','habits.js','improvements.js','proactive.js','web.html')}


def main():
    live=json.loads(remote("import pathlib,base64,json\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(sorted(FILES))+
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))"))
    before={};after={};anchors={}
    for name in sorted(FILES):
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        value=(ROOT/name).read_text(encoding='utf-8')
        if name in NEW:
            if raw is not None:raise RuntimeError('New source already exists: '+name)
        else:
            if raw is None:raise RuntimeError('Missing live source: '+name)
            saved=OUT/'server-before'/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(raw)
            previous=subprocess.check_output(['git','show',BASELINE+':'+name],cwd=ROOT).decode()
            if name.endswith('web.html'):
                value=raw.decode().replace('\r\n','\n')
                for asset in ('console.js','habits.js','improvements.js','proactive.js'):
                    value,count=re.subn(r'(/'+re.escape(asset)+r'\?v=)[^"\s]+',r'\g<1>20261029-quiet-controls',value)
                    if count!=1:raise RuntimeError('Changed asset tag: '+asset)
                anchors[name]=['four reviewed asset tags only']
            else:value,anchors[name]=merge_delta(previous,value,raw.decode(),name)
        if name.endswith('.py'):compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(value,encoding='utf-8',newline='\n')
        after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':before,'after':after,'unique_line_anchors':anchors},indent=2))
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in sorted(FILES):archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'files':len(FILES),'root':live['root'],'anchors':anchors}))


if __name__=='__main__':main()
