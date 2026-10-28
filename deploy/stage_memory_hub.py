"""Stage only this task's delta on the actual live release, preserving local WIP."""
import base64
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/memory-hub'
MODULES=['memory.py','learning.py','affection.py','conversation.py','context.py','store.py',
         'runtime.py','settings.py','app.py','console.js','web.html']
NEW=['whalegirl_linux/memory_hub.py','whalegirl_linux/memory-hub.js',
     'tests/test_memory_hub.py','tests/test_learning.py','tests/test_human_like_evolution.py',
     'tests/test_chat_personality.py']
FILES=['whalegirl_linux/'+n for n in MODULES]+NEW


def remote(code):
    return subprocess.run(['ssh.exe','-o','BatchMode=yes','-o','ConnectTimeout=10',
        'root@120.46.186.50','python3','-'],input=code,text=True,capture_output=True,
        encoding='utf-8',check=True).stdout


def merge_delta(before,after,live,name):
    a,b=before.splitlines(keepends=True),after.splitlines(keepends=True)
    changes=[op for op in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes() if op[0]!='equal']
    for tag,i,j,k,l in reversed(changes):
        match=None
        # Smallest unique unchanged anchors make unrelated live/local edits survive.
        for span in range(1,13):
            start=max(0,i-span);end=min(len(a),j+span)
            old=''.join(a[start:end]);new=''.join(a[start:i]+b[k:l]+a[j:end])
            if live.count(old)==1:
                match=(old,new);break
        if match is None:
            raise RuntimeError(f'Live delta needs review: {name}:{i+1}-{j} ({tag})')
        live=live.replace(*match,1)
    return live,len(changes)


def main():
    observed=json.loads((OUT/'before.json').read_text(encoding='utf-8'))
    live=json.loads(remote("import base64,json,pathlib\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(FILES)+
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))"))
    if live['root']!=observed['root']:raise RuntimeError('Active production release changed')
    before={};after={};deltas={}
    for name in FILES:
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        digest=hashlib.sha256(raw).hexdigest() if raw is not None else None
        if name in observed['before'] and observed['before'][name]!=digest:
            raise RuntimeError('Production source changed: '+name)
        before[name]=digest
        if name in NEW:
            if raw is not None:raise RuntimeError('New file already exists in production: '+name)
            content=(ROOT/name).read_text(encoding='utf-8')
            deltas[name]='new'
        elif name=='whalegirl_linux/web.html':
            content=raw.decode('utf-8')
            patches=[('/console.js?v=20261028-followup','/console.js?v=20261028-memory-hub'),
                ('<script defer src="/learning.js?v=20261026-bento-ui"></script>',
                 '<script defer src="/learning.js?v=20261026-bento-ui"></script>\n<script defer src="/memory-hub.js?v=20261028-unified"></script>')]
            for old,new in patches:
                if content.count(old)!=1:raise RuntimeError('Reviewed HTML anchor changed')
                content=content.replace(old,new,1)
            deltas[name]=len(patches)
        else:
            content,deltas[name]=merge_delta((OUT/'local-before'/Path(name).name).read_text(encoding='utf-8'),
                (ROOT/name).read_text(encoding='utf-8'),raw.decode('utf-8'),name)
        if name.endswith('.py'):compile(content,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(content,encoding='utf-8',newline='\n');after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    manifest={'root':live['root'],'before':before,'after':after,'deltas':deltas}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in FILES:archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'staged_files':len(FILES),'deltas':deltas},ensure_ascii=False))


if __name__=='__main__':main()
