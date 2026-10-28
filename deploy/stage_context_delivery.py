"""Stage this task's delta against live sources, preserving unrelated local work."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

from stage_memory_hub import merge_delta

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/context-delivery-v2'
MODULES=['context.py','store.py','runtime.py','gateway.py','safety.py','app.py','settings.py','console.js','web.html','proactive.py']
TESTS=['test_media_delivery.py','test_chat_habits.py','test_core.py','test_native_delivery.py']
EXISTING=['whalegirl_linux/'+n for n in MODULES]+['tests/'+n for n in TESTS]+['deploy/chat_select_linux.py']
NEW=['whalegirl_linux/delivery.py','tests/test_context_delivery.py']
UI='/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools/chat_select_linux.py'


def remote(code):
    result=subprocess.run(['ssh.exe','-o','BatchMode=yes','-o','ConnectTimeout=10','root@120.46.186.50','python3','-'],
        input=code,text=True,capture_output=True,encoding='utf-8',timeout=60)
    if result.returncode:raise RuntimeError(result.stderr[-1500:])
    return result.stdout


def fetch():
    code="import base64,json,pathlib\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(EXISTING+NEW)
    code+="\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((pathlib.Path("+repr(UI)+") if n=='deploy/chat_select_linux.py' else r/n).read_bytes()).decode() if (pathlib.Path("+repr(UI)+") if n=='deploy/chat_select_linux.py' else r/n).is_file() else None for n in names}}))"
    live=json.loads(remote(code))
    for name,value in live['files'].items():
        if value is not None:
            path=OUT/'server-before'/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(base64.b64decode(value))
    (OUT/'live-sources.json').write_text(json.dumps(live),encoding='utf-8')
    print('Fetched live source snapshot:',live['root'],len(live['files']))
    return live


def stage():
    live=json.loads((OUT/'live-sources.json').read_text(encoding='utf-8'))
    before={};after={};deltas={}
    for name in EXISTING+NEW:
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        if name in NEW:
            if raw is not None:raise RuntimeError('New task file exists remotely: '+name)
            value=(ROOT/name).read_text(encoding='utf-8');deltas[name]='new'
        else:
            previous=(OUT/'local-before'/name).read_text(encoding='utf-8')
            updated=(ROOT/name).read_text(encoding='utf-8')
            production=raw.decode('utf-8')
            if name=='whalegirl_linux/context.py':
                # Equivalent local refactoring was never published; retain the
                # live variable style while applying only this task's edits.
                previous=previous.replace('    context_limit=settings.context_messages\n','').replace('context_limit','settings.context_messages')
                updated=updated.replace('    context_limit=settings.context_messages\n','').replace('context_limit','settings.context_messages')
                previous=previous.replace('limit=min(settings.context_messages, max(6, settings.context_messages // 3))',
                                          'limit=min(10, max(6, settings.context_messages // 3))')
                live_line=next(line for line in production.splitlines(keepends=True) if '"【当前会话近期上下文｜"' in line)
                previous_lines=''.join(line for line in previous.splitlines(keepends=True)
                    if '"【当前会话近期完整消息' in line or '"以下是按当前话题筛选' in line)
                if production.count(live_line)!=1:raise RuntimeError('Context prompt anchor changed')
                production=production.replace(live_line,previous_lines,1)
            if name=='whalegirl_linux/web.html':
                patches=[('/console.js?v=20261028-memory-hub','/console.js?v=20261028-context-delivery'),
                    ('普通群消息也会进入上下文。','按时间顺序输入最近设置条数内的全部消息，不按关键词筛选；默认40条，重复采集只计一次。保留引用、表情信息和已有图片描述；超长内容受字符预算限制。'),
                    ('需使用 Chat Completions 接口并在模型设置中关闭流式接收。','需使用 Chat Completions 接口并在模型设置中关闭流式接收。 发送最多尝试2次（含首次）；回执待核实只记日志，后续聊天继续；连续明确失败才暂停。'),
                    ('连续3次明确失败会暂停','连续2次明确失败会暂停')]
                for old,new in patches:
                    if production.count(old)!=1:raise RuntimeError('Reviewed HTML anchor changed: '+old)
                    production=production.replace(old,new,1)
                value,count=production,len(patches)
            else:
                value,count=merge_delta(previous,updated,production,name)
            deltas[name]=count
        if name.endswith('.py'):compile(value,name,'exec')
        path=OUT/'payload'/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(value,encoding='utf-8',newline='\n')
        after[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    manifest={'root':live['root'],'before':before,'after':after,'deltas':deltas,'ui_path':UI}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in EXISTING+NEW:archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'staged':len(after),'deltas':deltas}))


if __name__=='__main__':
    import sys
    fetch() if '--fetch' in sys.argv else stage()
