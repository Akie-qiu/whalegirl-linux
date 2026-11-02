"""Stage a native lease and core switch without replacing unrelated changes."""
import base64
import hashlib
import json
from pathlib import Path
import re
import tarfile
from stage_tool_reply_guard import remote,merge_delta

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'artifacts/fast-turn'
NEW={'whalegirl_linux/fast_send.py','deploy/native_fast_turn_v1.py','tests/test_fast_send.py'}
FILES=NEW|{'deploy/chat_select_linux.py'}|{'whalegirl_linux/'+n for n in
    ('runtime.py','gateway.py','delivery.py','settings.py','proactive.js','app.py','console.js','web.html')}


def main():
    code="""import pathlib,base64,json,subprocess
r=pathlib.Path('/opt/whalegirl-linux/current').resolve()
names=NAMES
tools=pathlib.Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
mounted=subprocess.check_output(['docker','exec','whalegirl-wechat-candidate-4118','cat','/opt/tools/chat-select.py'])
native={n:base64.b64encode((tools/n).read_bytes()).decode() if (tools/n).is_file() else None for n in ('chat_select_linux.py','native_fast_turn_v1.py')}
print(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names},'mounted':base64.b64encode(mounted).decode(),'native':native}))
""".replace('NAMES',repr(sorted(FILES)))
    live=json.loads(remote(code));before={};after={}
    mounted=base64.b64decode(live['mounted'])
    host=base64.b64decode(live['native']['chat_select_linux.py'])
    if mounted!=host:raise RuntimeError('Mounted and persistent selector differ; review before deployment')
    for name in sorted(FILES):
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        if name in NEW:
            if raw is not None:raise RuntimeError('New file exists: '+name)
            value=(ROOT/name).read_text(encoding='utf-8')
        else:
            saved=OUT/'server-before'/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(raw)
            production=mounted.decode() if name=='deploy/chat_select_linux.py' else raw.decode()
            if name.endswith('web.html'):
                value=production
                for asset in ('proactive','console'):
                    value,count=re.subn(r'(/'+asset+r'\.js\?v=)[^"\s]+',r'\g<1>20261030-fast-turn',value)
                    if count!=1:raise RuntimeError('Asset version anchor changed: '+asset)
            else:
                value,_=merge_delta((OUT/'local-before'/name).read_text(encoding='utf-8'),
                    (ROOT/name).read_text(encoding='utf-8'),production,name)
        if name.endswith('.py'):compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(value,encoding='utf-8',newline='\n');after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    manifest={'root':live['root'],'before':before,'after':after,
        'native_before':{n:hashlib.sha256(base64.b64decode(v)).hexdigest() if v is not None else None for n,v in live['native'].items()},
        'mounted_before':hashlib.sha256(mounted).hexdigest()}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2))
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in sorted(FILES):archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'files':len(FILES),'root':live['root'],'native_selector_matches':True}))


if __name__=='__main__':main()
