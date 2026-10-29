"""Stage the unified chat console without unrelated local changes."""
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile

from stage_tool_reply_guard import merge_delta, remote

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/chat-control-merge'
BASELINE='047ef32'
FILES=['whalegirl_linux/'+name for name in ('app.py','console.js','habits.js','proactive.js','web.html')]+['tests/test_autonomy.py']


def main():
    code="import pathlib,base64,json\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(FILES)
    code+="\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() for n in names}}))"
    live=json.loads(remote(code)); before={};after={};anchors={}
    for name in FILES:
        raw=base64.b64decode(live['files'][name]);before[name]=hashlib.sha256(raw).hexdigest()
        previous=subprocess.check_output(['git','show',BASELINE+':'+name],cwd=ROOT).decode('utf-8')
        updated=(ROOT/name).read_text(encoding='utf-8')
        for folder,data in [('server-before',raw),('local-before',previous.encode())]:
            saved=OUT/folder/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(data)
        if name.endswith('web.html'):
            for script in ('console','habits','proactive'):
                pattern=r'/'+script+r'\.js\?v=[^"\s]+'
                old=re.search(pattern,previous).group()
                updated,count=re.subn(pattern,old,updated)
                if count!=1:raise RuntimeError('Expected one script: '+script)
        value,anchors[name]=merge_delta(previous,updated,raw.decode('utf-8'),name)
        if name.endswith('web.html'):
            for script in ('console','habits','proactive'):
                value,count=re.subn(r'/'+script+r'\.js\?v=[^"\s]+','/'+script+'.js?v=20261029-chat-controls',value)
                if count!=1:raise RuntimeError('Expected one live script: '+script)
        if name.endswith('.py'):compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(value,encoding='utf-8',newline='\n');after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':before,'after':after,'unique_line_anchors':anchors},indent=2))
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in FILES:archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'files':len(FILES),'root':live['root'],'unique_line_anchors':anchors}))


if __name__=='__main__':main()
