"""Apply this task's delta to the live release, retaining unrelated server edits."""
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile

from stage_tool_reply_guard import remote,merge_delta

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/chat-pipeline-repair'
BASE='96aaaddd4f7ed48190913516dd0ab99af7d341a3'
NEW={'tests/test_chat_pipeline_repair.py'}
FILES={'whalegirl_linux/context.py','whalegirl_linux/runtime.py','whalegirl_linux/gateway.py',
       'whalegirl_linux/proactive.py','whalegirl_linux/tieba.py','whalegirl_linux/upstream/ai_engine.py',
       'whalegirl_linux/tieba.js','whalegirl_linux/web.html'}|NEW


def main():
    live=json.loads(remote("import base64,json\nfrom pathlib import Path\nr=Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(sorted(FILES))+
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))"))
    before={};after={};anchors={}
    for name in sorted(FILES):
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        if name in NEW:
            if raw is not None:raise RuntimeError('New test already exists: '+name)
            value=(ROOT/name).read_text(encoding='utf-8')
        else:
            if raw is None:raise RuntimeError('Missing live source: '+name)
            saved=OUT/'server-before'/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(raw)
            if name=='whalegirl_linux/web.html':
                value=raw.decode();pattern=r'/tieba\.js\?v=[^"\s>]+'
                if len(re.findall(pattern,value))!=1:raise RuntimeError('Tieba script is not unique')
                value=re.sub(pattern,'/tieba.js?v=20261030-chat-pipeline',value,count=1)
                anchors[name]=['tieba_script_version_only']
            else:
                previous=subprocess.check_output(['git','show',BASE+':'+name],cwd=ROOT).decode()
                value,anchors[name]=merge_delta(previous,(ROOT/name).read_text(encoding='utf-8'),raw.decode(),name)
        if name.endswith('.py'):compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(value,encoding='utf-8',newline='\n')
        after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':before,'after':after,
        'unique_line_anchors':anchors},indent=2),encoding='utf-8')
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in sorted(FILES):archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'files':len(FILES),'root':live['root'],'unique_line_anchors':anchors}))


if __name__=='__main__':main()
