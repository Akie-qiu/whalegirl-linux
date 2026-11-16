"""Stage a prompt-preserving provider error patch on the audited live release."""
import argparse
import ast
import collections
import hashlib
import inspect
import json
from pathlib import Path
import subprocess

from deploy_sticker_picker_speed import release_snapshot

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'artifacts/content-filter-20261108'
OUT=AUDIT/'release-01'
REMOTE='root@120.46.186.50'
REMOTE_DIR='/opt/whalegirl-linux-assets/content-filter-diagnostics-20261108-01'
FILES=('whalegirl_linux/upstream/ai_engine.py','whalegirl_linux/model_reply.py',
       'whalegirl_linux/runtime.py','tests/test_content_filter.py')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deploy',action='store_true')
    args=parser.parse_args()
    if OUT.exists():raise RuntimeError('Staged artifacts already exist; preserve them')
    audit=json.loads((AUDIT/'audit.json').read_text(encoding='utf-8'))
    code='import hashlib,json,os\nfrom pathlib import Path\n'+inspect.getsource(release_snapshot)+"\nroot=Path('/opt/whalegirl-linux/current').resolve()\nprint(json.dumps({'root':str(root),'source_snapshot':release_snapshot(root)}))"
    response=subprocess.run(['ssh.exe','-o','BatchMode=yes','-o','ConnectTimeout=10',REMOTE,'python3','-'],
        input=code,capture_output=True,text=True,encoding='utf-8',check=True,timeout=50)
    live=json.loads(response.stdout)
    if live['root']!=audit['root'] or live['source_snapshot']!=audit['source_snapshot']:
        raise RuntimeError('Production changed since audit; no files uploaded or deployed')
    for name in FILES[:3]:
        old=ast.parse((AUDIT/'server-before'/name).read_text(encoding='utf-8'))
        new=ast.parse((ROOT/name).read_text(encoding='utf-8'))
        if name.endswith('ai_engine.py'):
            for method in ('_build_payload','_output_budget','_request_fingerprint'):
                one=next(n for n in ast.walk(old) if isinstance(n,ast.FunctionDef) and n.name==method)
                two=next(n for n in ast.walk(new) if isinstance(n,ast.FunctionDef) and n.name==method)
                if ast.dump(one)!=ast.dump(two):raise RuntimeError('Model request construction changed')
        else:
            one=collections.Counter(n.value for n in ast.walk(old) if isinstance(n,ast.Constant) and isinstance(n.value,str))
            two=collections.Counter(n.value for n in ast.walk(new) if isinstance(n,ast.Constant) and isinstance(n.value,str))
            if one-two:raise RuntimeError('An existing prompt/string was removed: '+name)
    after={}
    for name in FILES:
        content=(ROOT/name).read_text(encoding='utf-8').encode('utf-8')
        compile(content,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(content);after[name]=hashlib.sha256(content).hexdigest()
    manifest={**live,'after':after,'prompts_changed':False,'configuration_changed':False}
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=True,indent=2),encoding='utf-8')
    if not args.deploy:
        print(json.dumps({'staged':str(OUT),'root':live['root'],'files':FILES}))
        return
    subprocess.run(['ssh.exe','-o','BatchMode=yes',REMOTE,'mkdir','-m','700',REMOTE_DIR],check=True,timeout=30)
    subprocess.run(['scp.exe','-q','-r',str(OUT/'payload'),str(OUT/'manifest.json'),
                    str(ROOT/'deploy/deploy_content_filter.py'),REMOTE+':'+REMOTE_DIR+'/'],check=True,timeout=120)
    deployment=subprocess.run(['ssh.exe','-o','BatchMode=yes',REMOTE,'python3',
        REMOTE_DIR+'/deploy_content_filter.py',REMOTE_DIR],capture_output=True,text=True,encoding='utf-8',timeout=360)
    (OUT/'deployment.stdout.log').write_text(deployment.stdout,encoding='utf-8')
    (OUT/'deployment.stderr.log').write_text(deployment.stderr,encoding='utf-8')
    if deployment.returncode:raise RuntimeError('Deployment did not complete: '+deployment.stderr[-2000:])
    report=json.loads(deployment.stdout)
    (OUT/'deployment.json').write_text(json.dumps(report,ensure_ascii=True,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))


if __name__=='__main__':main()
