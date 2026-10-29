"""Apply only this feature's delta to core and native UI helpers."""
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
from stage_tool_reply_guard import merge_delta, remote

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/ui-recovery-merge'
BASELINE='752672a'
NATIVE={'deploy/chat_select_linux.py','deploy/native_recovery.py'}
NEW={'whalegirl_linux/recovery.py','whalegirl_linux/recovery.js','whalegirl_linux/recovery.css',
     'tests/test_ui_recovery.py','deploy/native_recovery.py'}
FILES=NEW|{'whalegirl_linux/'+n for n in ('settings.py','safety.py','gateway.py','runtime.py','images.py','app.py','console.js','web.html')}|NATIVE
HINTS=[('连续2次明确失败会暂停；系统异常提示需处理后人工恢复。','连续2次明确失败先自动恢复微信界面，恢复后仍失败才暂停；相关操作集中在顶部“异常与恢复”。'),
       ('总开关即时生效。发送结果不确定时，请在处理记录中核对。','总开关即时生效。异常恢复和待核对记录集中在页面顶部。')]
ASSETS='<link rel="stylesheet" href="/recovery.css?v=20261029"><script defer src="/recovery.js?v=20261029"></script>'


def main():
    code=("import pathlib,base64,json,subprocess,hashlib\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\ntools=pathlib.Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')\nfiles="+repr(sorted(FILES))+"\nnative="+repr(NATIVE)+
          "\ndef source(n):return tools/pathlib.Path(n).name if n in native else r/n\nmounted=subprocess.check_output(['docker','exec','whalegirl-wechat-candidate-4118','cat','/opt/tools/chat-select.py'])\nprint(json.dumps({'root':str(r),'mounted_before':hashlib.sha256(mounted).hexdigest(),'files':{n:base64.b64encode(source(n).read_bytes()).decode() if source(n).is_file() else None for n in files}}))")
    live=json.loads(remote(code));before={};after={};anchors={}
    reviewed=OUT/'mounted-before/chat_select_linux.py'
    if not reviewed.is_file() or hashlib.sha256(reviewed.read_bytes()).hexdigest()!=live['mounted_before']:
        raise RuntimeError('Running native helper changed since the source/mount review')
    for name in sorted(FILES):
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        updated=(ROOT/name).read_text(encoding='utf-8')
        if name in NEW:
            if raw is not None:raise RuntimeError('New feature file already exists: '+name)
            value=updated
        else:
            previous=subprocess.check_output(['git','show',BASELINE+':'+name],cwd=ROOT).decode('utf-8')
            for folder,data in [('server-before',raw),('local-before',previous.encode())]:
                saved=OUT/folder/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(data)
            if name.endswith('web.html'):
                previous_tag=re.search(r'/console\.js\?v=[^"\s]+',previous).group()
                updated=re.sub(r'/console\.js\?v=[^"\s]+',previous_tag,updated)
                updated=updated.replace(ASSETS+'\n','')
                for old,new in HINTS:updated=updated.replace(new,old)
            value,anchors[name]=merge_delta(previous,updated,raw.decode('utf-8'),name)
            if name.endswith('web.html'):
                if value.count('</head>')!=1:raise RuntimeError('Unexpected head anchor')
                value=value.replace('</head>',ASSETS+'\n</head>',1)
                value=re.sub(r'/console\.js\?v=[^"\s]+','/console.js?v=20261029-recovery',value)
                for old,new in HINTS:
                    if value.count(old)!=1:raise RuntimeError('Unexpected recovery hint anchor')
                    value=value.replace(old,new,1)
        if name.endswith('.py'):compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(value,encoding='utf-8',newline='\n');after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'mounted_before':live['mounted_before'],'before':before,'after':after,'unique_line_anchors':anchors},indent=2))
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in sorted(FILES):archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'files':len(FILES),'root':live['root'],'unique_line_anchors':anchors}))


if __name__=='__main__':main()
