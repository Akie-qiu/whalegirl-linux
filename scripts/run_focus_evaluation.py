"""Linux sandbox supervisor; bounds host impact and keeps every failed result."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import time
import urllib.request

CORE=Path('/var/lib/whalegirl-linux/core')


def host():
    mem={line.split(':')[0]:int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if ':' in line}
    psi=Path('/proc/pressure/memory').read_text().splitlines()[0].split()[1:]
    return {'at':time.time(),'available_mib':round(mem['MemAvailable']/1024,2),
        'swap_used_mib':round((mem['SwapTotal']-mem['SwapFree'])/1024,2),
        'load_1m':os.getloadavg()[0],'memory_psi':dict(item.split('=') for item in psi)}


def protected():
    return {name:hashlib.sha256((CORE/name).read_bytes()).hexdigest() for name in
        ('settings.json','persona-cards.json','model-profiles.json','model-api-keys.enc','bilibili-session.enc') if (CORE/name).exists()}


def health():
    try:
        token=(CORE/'admin.token').read_text().strip();started=time.monotonic()
        req=urllib.request.Request('http://127.0.0.1:8092/api/state',headers={'Authorization':'Bearer '+token})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=4) as response:data=json.load(response)
        return {'at':time.time(),'seconds':round(time.monotonic()-started,4),'enabled':data.get('enabled'),
            'gateway':data.get('gateway',{}).get('status'),'paused':bool(data.get('paused_reason'))}
    except Exception as error:return {'at':time.time(),'error':type(error).__name__}


def write(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2));path.chmod(0o600)


def run(root,cmd,label,report):
    folder=root/'runs'/label;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    started=time.monotonic();samples=[];stopped=''
    with (folder/'process.log').open('w') as log:
        process=subprocess.Popen([*cmd,'--out',str(folder)],stdout=log,stderr=log,env={**os.environ,'LOGURU_LEVEL':'ERROR','PYTHONUNBUFFERED':'1'})
        while process.poll() is None:
            sample=host();samples.append(sample)
            if sample['available_mib']<80:
                stopped='host_memory_guard';process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            if time.monotonic()-started>190:
                stopped='case_timeout';process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            time.sleep(.5)
    result_path=folder/'result.json'
    result=json.loads(result_path.read_text()) if result_path.exists() else {}
    item={'label':label,'seconds':round(time.monotonic()-started,3),'exit_code':process.returncode,
        'stop':stopped or result.get('error_code',''),'result':str(result_path),
        'samples':samples,'health_after':health(),'peak_rss_mib':result.get('peak_rss_mib')}
    report['runs'].append(item)
    write(root/'progress.json',report)
    return not item['stop'] and process.returncode==0


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--followup',action='store_true');args=parser.parse_args()
    root=args.root.resolve()
    if not root.name.startswith('whalegirl-focus-eval-20261105-') or root.parent!=Path('/tmp'):raise RuntimeError('invalid_sandbox')
    (root/'runs').mkdir(mode=0o700,exist_ok=True)
    before=protected();report={'started':time.time(),'baseline_health':health(),'baseline_resources':host(),
        'resource_limits':{'memory_max_mib':192,'cpu_quota_percent':50,'host_stop_mib':80},'runs':[]}
    if args.followup and (root/'progress.json').exists():
        report=json.loads((root/'progress.json').read_text());report.pop('finished',None)
        report['followup_started']=time.time()
    write(root/'progress.json',report)
    python='/opt/whalegirl-linux/venv/bin/python';harness=root/'evaluate_focus_chat.py'
    command=[python,str(harness)]
    # Pressure first: refuse the model replay if the source-only worker cannot
    # coexist safely with production in its isolated resource envelope.
    for label,arm,context in ([] if args.followup else [('pressure-baseline100','baseline',100),('pressure-candidate100','candidate',100),('pressure-candidate40','candidate',40)]):
        if not run(root,[*command,'--code',str(root/arm),'--context',str(context),'--pressure'],label,report):
            report['aborted']='resource_or_pressure_check';break
    if not report.get('aborted'):
        rng=random.Random(20261105);cases=['others','resolved','topic_owner'] if args.followup else ['topic','tired','others','resolved','banter','preference']
        rng.shuffle(cases)
        for case in cases:
            arms=['baseline','candidate'];rng.shuffle(arms)
            for arm in arms:
                if protected()!=before:
                    report['aborted']='production_configuration_changed';break
                label=('probe-' if args.followup else '')+case+'-'+arm
                options=['--natural-mode'] if args.followup and case!='topic_owner' else []
                if not run(root,[*command,'--code',str(root/arm),'--context','100','--case',case,*options],label,report):
                    report['aborted']='replay_incomplete';break
            if report.get('aborted'):break
    report.update(finished=time.time(),final_health=health(),final_resources=host(),
                  protected_configuration_unchanged=protected()==before)
    write(root/'progress.json',report)
    print(json.dumps({'runs':len(report['runs']),'aborted':report.get('aborted',''),
        'configuration_unchanged':report['protected_configuration_unchanged']},ensure_ascii=True),flush=True)


if __name__=='__main__':main()
