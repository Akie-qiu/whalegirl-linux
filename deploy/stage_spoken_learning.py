"""Stage only speech learning against live sources, retaining unfinished work."""
import ast
import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

from stage_tool_reply_guard import merge_delta

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/spoken-learning-deployment-20261105'
FILES={'whalegirl_linux/growth.py','whalegirl_linux/growth_memory.py',
       'whalegirl_linux/expression_reference.py','whalegirl_linux/bilibili.py',
       'whalegirl_linux/growth.js','tests/test_video_speech.py','tests/test_expression_reference.py'}


def remote(code):
    result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','root@120.46.186.50',
        '/opt/whalegirl-linux/venv/bin/python','-'],input=code,text=True,encoding='utf-8',capture_output=True,timeout=45)
    if result.returncode:raise RuntimeError('source_snapshot_failed')
    return json.loads(result.stdout)


def method(content,cls,name):
    node=next(n for n in ast.parse(content).body if isinstance(n,ast.ClassDef) and n.name==cls)
    node=next(n for n in node.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
    start=min([node.lineno,*[n.lineno for n in node.decorator_list]])-1
    return start,node.end_lineno


def replace_method(live,local,cls,name):
    left,right=method(live,cls,name);first,last=method(local,cls,name)
    lines=live.splitlines(keepends=True);lines[left:right]=local.splitlines(keepends=True)[first:last]
    return ''.join(lines)


def main():
    cached=ROOT/'artifacts/focus-evaluation-20261105'
    expected=json.loads((cached/'stage.json').read_text())['hashes']
    wanted=sorted(FILES|{'tests/test_bilibili.py','tests/test_chat_habits.py'})
    packet=remote('''import pathlib,base64,json,hashlib
root=pathlib.Path('/opt/whalegirl-linux/current').resolve();expected=EXPECTED;changed=[]
for name,digest in expected.items():
 path=root/name
 if not path.is_file() or hashlib.sha256(path.read_bytes().replace(b'\\r\\n',b'\\n')).hexdigest()!=digest:changed.append(name)
names=WANTED
print(json.dumps({'root':str(root),'changed_since_baseline':changed,'files':{name:base64.b64encode((root/name).read_bytes()).decode() if (root/name).is_file() else None for name in names}}))
'''.replace('EXPECTED',repr(expected)).replace('WANTED',repr(wanted)))
    if packet['changed_since_baseline']:raise RuntimeError('source_baseline_changed: '+','.join(packet['changed_since_baseline']))
    OUT.mkdir(parents=True,exist_ok=True);validation=OUT/'validation'
    if validation.exists():raise RuntimeError('validation_exists_preserve_previous_stage')
    shutil.copytree(cached/'baseline',validation)
    before={};after={};original={}
    for name,encoded in packet['files'].items():
        if encoded is None:continue
        path=validation/name
        if not path.resolve().is_relative_to(validation.resolve()):raise RuntimeError('invalid_source_path')
        data=base64.b64decode(encoded);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        if name in FILES:original[name]=data
    for name in sorted(FILES):
        local=(ROOT/name).read_text('utf-8');raw=original.get(name)
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        live=raw.decode('utf-8').replace('\r\n','\n') if raw is not None else None
        if name=='whalegirl_linux/growth.py':
            value=live
            for fn in ('source_excerpt','reflect_sources'):value=replace_method(value,local,'GrowthService',fn)
        elif name=='whalegirl_linux/growth_memory.py':
            value=replace_method(live,local,'GrowthMemoryPolicy','register')
        elif name=='whalegirl_linux/bilibili.py':
            common=(ROOT/'artifacts/focus-evaluation-20261105/candidate'/name).read_text('utf-8')
            value,_=merge_delta(common,local,live,name)
        elif name=='whalegirl_linux/growth.js':
            common=subprocess.run(['git','show','HEAD:'+name],cwd=ROOT,capture_output=True,check=True).stdout.decode()
            value,_=merge_delta(common,local,live,name)
        else:
            if raw is not None:raise RuntimeError('new_file_already_exists: '+name)
            value=local
        value=value.replace('\r\n','\n')
        if name.endswith('.py'):compile(value,name,'exec')
        payload=OUT/'payload'/name;payload.parent.mkdir(parents=True,exist_ok=True);payload.write_text(value,'utf-8',newline='\n')
        target=validation/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(payload.read_bytes())
        after[name]=hashlib.sha256(payload.read_bytes()).hexdigest()
        if raw is not None:
            saved=OUT/'server-before'/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(raw)
    # These entry points must remain exactly as deployed. No pending @ feature
    # or unrelated reply selection is included in this release.
    old=original['whalegirl_linux/growth.py'].decode().replace('\r\n','\n')
    new=(validation/'whalegirl_linux/growth.py').read_text('utf-8')
    for fn in ('request','prompt','learn_bilibili','run_once'):
        a,b=method(old,'GrowthService',fn);c,d=method(new,'GrowthService',fn)
        if old.splitlines()[a:b]!=new.splitlines()[c:d]:raise RuntimeError('unrelated_method_changed: '+fn)
    manifest={'root':packet['root'],'before':before,'after':after,'scope':'spoken_learning_only'}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),'utf-8')
    with tarfile.open(OUT/'release.tar.gz','w:gz') as archive:
        for name in sorted(FILES):archive.add(OUT/'payload'/name,arcname=name,recursive=False)
    print(json.dumps({'root':packet['root'],'code_files':5,'test_files':2,
                      'archive_bytes':(OUT/'release.tar.gz').stat().st_size,'validation':str(validation)}))


if __name__=='__main__':main()
