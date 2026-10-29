"""Merge only this task's delta into the live core, retaining other fixes."""
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
from stage_tool_reply_guard import merge_delta, remote

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/unified-actions-merge'
BASELINE='6c9030a'
NEW={'tests/test_unified_actions.py'}
FILES=NEW|{'whalegirl_linux/'+name for name in (
    'context.py','conversation.py','proactive.py','runtime.py','settings.py',
    'improvements.js','proactive.js','web.html')}|{'tests/test_reply_expression.py','tests/test_autonomy.py'}


def main():
    live=json.loads(remote("import pathlib,base64,json\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(sorted(FILES))+
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))"))
    before={};after={};anchors={}
    for name in sorted(FILES):
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        updated=(ROOT/name).read_text(encoding='utf-8')
        if raw is None:
            if name not in NEW and not name.startswith('tests/'):raise RuntimeError('Missing live source: '+name)
            value=updated
        else:
            if name in NEW:raise RuntimeError('New file already exists: '+name)
            previous=subprocess.check_output(['git','show',BASELINE+':'+name],cwd=ROOT).decode('utf-8')
            for folder,data in [('server-before',raw),('local-before',previous.encode())]:
                saved=OUT/folder/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(data)
            if name=='whalegirl_linux/web.html':
                value=raw.decode('utf-8').replace('\r\n','\n')
                old='id="reply-max-parts" type="number" min="1" max="3"'
                if value.count(old)!=1:raise RuntimeError('Changed reply capacity control')
                value=value.replace(old,old.replace('max="3"','max="6"'),1)
                for asset in ('improvements.js','proactive.js'):
                    value,count=re.subn(r'(/'+re.escape(asset)+r'\?v=)[^"\s]+',r'\g<1>20261029-unified-actions',value)
                    if count!=1:raise RuntimeError('Changed asset tag: '+asset)
                anchors[name]=['reviewed capacity and two asset tags only']
            else:
                if name=='whalegirl_linux/context.py':
                    # Live renamed this heading in an independent topic fix.
                    # Align unchanged context for the insertion, preserving it.
                    old='【本次需要回复的消息】'
                    current='【当前需要回复的消息｜本轮主题优先】'
                    if current not in raw.decode('utf-8'):raise RuntimeError('Changed live context heading')
                    previous=previous.replace(old,current)
                    updated=updated.replace(old,current)
                value,anchors[name]=merge_delta(previous,updated,raw.decode('utf-8'),name)
        if name.endswith('.py'):compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(value,encoding='utf-8',newline='\n');after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':before,'after':after,'unique_line_anchors':anchors},indent=2))
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in sorted(FILES):archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'files':len(FILES),'root':live['root'],'unique_line_anchors':anchors}))


if __name__=='__main__':main()
