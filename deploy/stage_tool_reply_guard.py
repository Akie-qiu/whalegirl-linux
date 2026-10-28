"""Stage guarded tool replies without shipping unrelated local changes."""
import base64
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/tool-reply-guard'
EXISTING=['whalegirl_linux/runtime.py','whalegirl_linux/tieba_tools.py']
NEW=['whalegirl_linux/model_reply.py','tests/test_tool_reply_guard.py']
FILES=EXISTING+NEW


def remote(code):
    return subprocess.run(['ssh.exe','-o','BatchMode=yes','-o','ConnectTimeout=10','root@120.46.186.50','python3','-'],
        input=code,text=True,capture_output=True,encoding='utf-8',check=True,timeout=60).stdout


def merge_delta(before,after,live,name):
    before,after,live=(value.replace('\r\n','\n') for value in (before,after,live))
    if before==live:
        return after,[]
    a,b=before.splitlines(keepends=True),after.splitlines(keepends=True)
    changes=[v for v in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes() if v[0]!='equal']
    anchors=[]
    for tag,i,j,k,l in reversed(changes):
        patch=None
        for size in range(1,13):
            start=max(0,i-size);end=min(len(a),j+size)
            old=''.join(a[start:end]);new=''.join(a[start:i]+b[k:l]+a[j:end])
            if live.count(old)==1:patch=(old,new);break
        if patch is None:
            # Exact unique changed lines suffice when the neighbouring live
            # token-accounting annotation differs from unfinished local work.
            old,new=''.join(a[i:j]),''.join(b[k:l])
            if not old or live.count(old)!=1:raise RuntimeError(f'Review live delta: {name}:{i+1}-{j}')
            patch=(old,new);anchors.append(i+1)
        live=live.replace(*patch,1)
    return live,anchors


def main():
    live=json.loads(remote("import pathlib,base64,json\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(FILES)+
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))"))
    before={};after={};reviewed={}
    for name in FILES:
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        if name in NEW:
            if raw is not None:raise RuntimeError('New file exists remotely: '+name)
            value=(ROOT/name).read_text(encoding='utf-8')
        else:
            saved=OUT/'server-before'/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(raw)
            production=raw.decode('utf-8')
            if name=='whalegirl_linux/tieba_tools.py':
                old="            raw = await engine.chat(history, system_prompt=system, max_tokens=max_tokens, response_format={'type': 'json_object'})\n"
                equivalent="            raw = await engine.chat(history, system_prompt=system, max_tokens=max_tokens,\n                                    response_format={'type': 'json_object'}, operation='tieba')\n"
                if production.count(old)!=1:raise RuntimeError('Reviewed Tieba follow-up anchor changed')
                production=production.replace(old,equivalent,1)
            value,anchors=merge_delta((OUT/'local-before'/name).read_text(encoding='utf-8'),
                (ROOT/name).read_text(encoding='utf-8'),production,name)
            reviewed[name]=anchors
        compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(value,encoding='utf-8',newline='\n');after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':before,'after':after,'unique_line_anchors':reviewed},indent=2),encoding='utf-8')
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in FILES:archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'staged':len(FILES),'root':live['root'],'unique_line_anchors':reviewed}))


if __name__=='__main__':main()
