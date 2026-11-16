"""Core-only rollback, with dynamic model configuration compatibility.

Always snapshots the CURRENT private configuration; never restores a business DB.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

LEGACY=('flash','pro','responses','custom')
FIELDS=('api_url','api_style','model','max_tokens','model_temperature','reasoning_effort','model_stream_enabled')

def adapt(settings, profiles, keys):
    settings=copy.deepcopy(settings);profiles=copy.deepcopy(profiles);keys=dict(keys)
    records=profiles.get('profiles',{})
    active=profiles.get('active') or settings.get('active_model_profile','custom')
    worker=settings.get('growth_internet_model_profile','current')
    if active not in records:raise ValueError('Active profile not available for rollback')
    if worker!='current' and worker not in records:raise ValueError('Growth profile not available for rollback')
    required=list(dict.fromkeys([active]+([] if worker=='current' else [worker])))
    used={p for p in required if p in LEGACY};mapping={p:p for p in used}
    for identity in required:
        if identity in mapping:continue
        slot=next(p for p in ('custom','responses','pro','flash') if p not in used)
        used.add(slot);mapping[identity]=slot
        records[slot]={k:v for k,v in records[identity].items() if k in FIELDS}
        keys[slot]=keys.get(identity,'')
    profiles['active']=mapping[active]
    # The snapshot retains overwritten slots, all metadata, and dynamic keys.
    # Unknown records are also retained here; legacy code simply ignores them.
    settings.update({k:v for k,v in records[mapping[active]].items() if k in FIELDS})
    settings['active_model_profile']=mapping[active]
    settings['growth_internet_model_profile']='current' if worker=='current' else mapping[worker]
    return settings,profiles,keys,mapping

def write_private(path, content):
    path=Path(path);temporary=path.with_name(path.name+'.rollback-pending')
    reference=path.stat() if path.exists() else path.parent.stat()
    fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(content)
    os.chown(temporary,reference.st_uid,reference.st_gid);os.replace(temporary,path);path.chmod(0o600)

def restore_compatibility(data):
    from cryptography.fernet import Fernet
    cipher=Fernet((data/'encryption.key').read_bytes().strip())
    settings=json.loads((data/'settings.json').read_text())
    profiles=json.loads((data/'model-profiles.json').read_text())
    keys=json.loads(cipher.decrypt((data/'model-api-keys.enc').read_bytes()))
    changed=profiles.get('active') not in LEGACY or settings.get('growth_internet_model_profile','current') not in ('current',*LEGACY)
    if not changed:return {}
    settings,profiles,keys,mapping=adapt(settings,profiles,keys)
    write_private(data/'model-api-keys.enc',cipher.encrypt(json.dumps(keys,ensure_ascii=False).encode()))
    write_private(data/'model-profiles.json',json.dumps(profiles,ensure_ascii=False,indent=2).encode())
    write_private(data/'settings.json',json.dumps(settings,ensure_ascii=False,indent=2).encode())
    return mapping

def main():
    import fcntl
    import tarfile
    import deploy_moments_recovery as rollout
    context=json.loads(Path(sys.argv[1]).read_text())
    with open(rollout.ASSETS/'model-console-20261116-deploy.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        previous=Path(context['previous']);candidate=Path(context['release'])
        if (rollout.BASE/'current').resolve()!=candidate:raise RuntimeError('Current release differs; rollback stopped')
        if not previous.is_dir():raise RuntimeError('Previous release is missing')
        start=time.monotonic()
        while rollout.api('state').get('processing') or rollout.inflight():
            if time.monotonic()-start>90:raise RuntimeError('Core is busy; retry rollback later')
            time.sleep(1)
        snapshot=Path(sys.argv[1]).parent/('rollback-current-config-'+str(time.time_ns()))
        snapshot.mkdir(mode=0o700)
        names=[p.name for p in rollout.DATA.iterdir() if p.is_file() and
            (p.suffix in ('.enc','.key','.token') or p.name in ('settings.json','persona-cards.json','model-profiles.json','console-password.json'))]
        switched=False;stopped=False
        try:
            subprocess.run(['systemctl','stop','whalegirl-linux.service'],check=True,timeout=40);stopped=True
            if rollout.inflight():raise RuntimeError('A send crossed shutdown; rollback stopped')
            with tarfile.open(snapshot/'private-config.tar.gz','w:gz') as archive:
                for name in names:archive.add(rollout.DATA/name,arcname=name,recursive=False)
            (snapshot/'private-config.tar.gz').chmod(0o600)
            mapping=restore_compatibility(rollout.DATA)
            rollout.DEPLOYMENT='model-console-rollback'
            rollout.switch(previous,str(time.time_ns()));switched=True
            subprocess.run(['systemctl','start','whalegirl-linux.service'],check=True,timeout=30)
            for _ in range(60):
                try:
                    state=rollout.api('state')
                    if state.get('gateway',{}).get('status')=='logged_in' and not state.get('paused_reason'):break
                except Exception:pass
                time.sleep(.5)
            else:raise RuntimeError('Rollback health not confirmed')
            result={'previous_release_restored':True,'dynamic_profile_mapping':mapping,
                'current_configuration_snapshot':str(snapshot),'business_database_restored':False,
                'wechat':state.get('gateway',{}).get('status')}
            (snapshot/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
        except BaseException:
            if stopped:
                subprocess.run(['systemctl','stop','whalegirl-linux.service'],check=False,timeout=40)
                if (snapshot/'private-config.tar.gz').exists():
                    with tarfile.open(snapshot/'private-config.tar.gz') as archive:
                        for item in archive.getmembers():
                            if item.name not in names or not item.isfile():raise RuntimeError('Unexpected snapshot entry')
                            write_private(rollout.DATA/item.name,archive.extractfile(item).read())
                if switched:rollout.switch(candidate,str(time.time_ns()))
                subprocess.run(['systemctl','start','whalegirl-linux.service'],check=False,timeout=30)
            raise

if __name__=='__main__':main()
