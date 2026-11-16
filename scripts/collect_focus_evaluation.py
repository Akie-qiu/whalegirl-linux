"""Collect synthetic results only, and prepare an anonymous readable comparison."""
import base64
import html
import json
from pathlib import Path
import random
import subprocess

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/focus-evaluation-20261105'


def collect():
    run=json.loads((OUT/'run.json').read_text())
    code='''import base64,json,pathlib
root=pathlib.Path(ROOT)
if root.parent!=pathlib.Path('/tmp') or not root.name.startswith('whalegirl-focus-eval-20261105-'):raise RuntimeError('invalid_sandbox')
names=[root/'progress.json',root/'supervisor.log',*sorted((root/'runs').glob('*/result.json'))]
files={}
for path in names:
 if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root) and path.stat().st_size<8*1024*1024:
  files[str(path.relative_to(root))]=base64.b64encode(path.read_bytes()).decode()
print(json.dumps(files))
'''.replace('ROOT',repr(run['root']))
    result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','root@120.46.186.50',
        '/opt/whalegirl-linux/venv/bin/python','-'],input=code,capture_output=True,text=True,encoding='utf-8',timeout=25)
    if result.returncode:raise RuntimeError('test_result_read_failed')
    destination=OUT/'results';destination.mkdir(exist_ok=True)
    for name,raw in json.loads(result.stdout).items():
        target=destination/name
        if not target.resolve().is_relative_to(destination.resolve()):raise RuntimeError('invalid_test_path')
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(base64.b64decode(raw))
    progress=json.loads((destination/'progress.json').read_text())
    pairs=[];mapping={};rng=random.Random(705113)
    for case in ('topic','tired','others','resolved','banter','preference','topic_owner'):
        variants=[]
        for arm in ('baseline','candidate'):
            path=destination/'runs'/(case+'-'+arm)/'result.json'
            probe=destination/'runs'/('probe-'+case+'-'+arm)/'result.json'
            if probe.exists():path=probe
            if not path.exists():continue
            raw=json.loads(path.read_text())
            variants.append((arm,raw))
        if len(variants)!=2:continue
        rng.shuffle(variants);pair={'case':case,'title':variants[0][1].get('title'),'variants':[]}
        for label,(arm,data) in zip(('甲','乙'),variants):
            mapping[case+'/'+label]=arm
            pair['variants'].append({'label':label,'turns':[{key:turn.get(key) for key in
                ('sender','input','state','error','sent')} for turn in data.get('turns',[])],
                'test_natural_mode':data.get('config',{}).get('test_natural_mode',False)})
        pairs.append(pair)
    (OUT/'anonymous.json').write_text(json.dumps(pairs,ensure_ascii=False,indent=2),'utf-8')
    (OUT/'mapping.json').write_text(json.dumps(mapping,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({'completed':len(progress.get('runs',[])),'pairs':len(pairs),'finished':progress.get('finished'),
        'aborted':progress.get('aborted'),'last_run':(progress.get('runs') or [{}])[-1].get('label')},ensure_ascii=True))


if __name__=='__main__':collect()
