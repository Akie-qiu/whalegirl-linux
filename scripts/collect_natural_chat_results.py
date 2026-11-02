"""Collect synthetic replay evidence and read-only metrics; never production DB."""
import json
import subprocess
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/natural-chat-20261102'
SCRIPT=r'''
import json,pathlib,sqlite3
roots=['/tmp/natural-chat-20261102-65zd2i4h','/tmp/natural-chat-20261102-xh7_prpl',
       '/tmp/natural-chat-20261102-j5bcw0kz','/tmp/natural-chat-20261102-qpctqt5u']
files={};metrics=[]
for n,raw in enumerate(roots):
    root=pathlib.Path(raw)
    for p in (root/'results').glob('*.json'):
        if '-progress' not in p.name:files[f'run{n+1}-{p.name}']=p.read_text()
    for p in root.glob('*regressions*.log'):files[f'run{n+1}-{p.name}']=p.read_text()
    for p in (root/'results').glob('replay-natural-*/state.sqlite3'):
        db=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
        try:
            for row in db.execute('SELECT i.content,i.key,m.metrics FROM inbox i JOIN reply_metrics m ON i.key=m.key'):
                data=json.loads(row['metrics']);memory=data.get('long_term_memory',{})
                arm='baseline' if 'chat_style_version' not in memory else 'learned' if memory.get('chat_style_ids') else 'compact'
                metrics.append({'run':n+1,'arm':arm,'content':row['content'],'key':row['key'],'metrics':data,
                                'sandbox':str(p.parent)})
        finally:db.close()
print(json.dumps({'files':files,'metrics':metrics},ensure_ascii=True))
'''


def main():
    result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','root@120.46.186.50',
        '/opt/whalegirl-linux/venv/bin/python','-'],input=SCRIPT,text=True,encoding='utf-8',capture_output=True,timeout=60)
    if result.returncode:raise RuntimeError(result.stderr[-1000:])
    data=json.loads(result.stdout)
    for name,content in data['files'].items():(OUT/name).write_text(content,'utf-8')
    (OUT/'replay-metrics.json').write_text(json.dumps(data['metrics'],ensure_ascii=False,indent=2),'utf-8')
    baseline=json.loads(data['files']['run1-baseline.json'])
    corrected=json.loads(data['files']['run3-baseline.json'])['turns'][0]
    baseline['turns']=[corrected if t['turn'].startswith('C2') else t for t in baseline['turns']]
    comparison={'baseline':baseline,'compact':json.loads(data['files']['run3-compact.json']),
                'learned':json.loads(data['files']['run3-learned.json']),
                'learning_source':'run2-learning.json; actual extracted .78/.72 proposals; current automatic acceptance policy; no handwritten notes',
                'note':'baseline C2 uses corrected nodding-sticker matcher, same release/settings. Native delivery not tested.'}
    for arm,report in comparison.items():
        if not isinstance(report,dict):continue
        run=3 if arm!='baseline' else None
        for turn in report['turns']:
            candidates=[m for m in data['metrics'] if m['arm']==arm and m['content']==turn['incoming'][0]['content']
                        and (m['run']==run if run else m['run']==(3 if turn['turn'].startswith('C2') else 1))]
            if len(candidates)==1:turn['metrics']=candidates[0]['metrics'];turn['sandbox']=candidates[0]['sandbox']
    (OUT/'comparison.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({'reports':len(data['files']),'metric_rows':len(data['metrics']),
        'states':{arm:[t['state'] for t in comparison[arm]['turns']] for arm in ('baseline','compact','learned')}}))


if __name__=='__main__':main()
