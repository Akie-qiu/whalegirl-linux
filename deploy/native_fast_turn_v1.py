"""An exclusively guarded native chat session; each command crosses once."""
import json
import os
from pathlib import Path
import re
import time


def write(path,value):
    pending=path.with_name(path.name+'.pending')
    fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    # Native jobs run as root in the container, while the core runs as the
    # private jobs directory's owner. Atomic replacement must preserve access
    # for that account, including newly created per-action result files.
    if hasattr(os,'fchown') and os.geteuid()==0:
        owner=path.parent.stat()
        os.fchown(fd,owner.st_uid,owner.st_gid)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False)
    os.replace(pending,path)


def check_guard(part,path):
    nonce=part.get('fast_session_nonce','')
    if not re.fullmatch('[0-9a-f]{32}',nonce):raise ValueError('Fast session identity is invalid')
    value=json.loads((path.parent/(nonce+'.guard.json')).read_text(encoding='utf-8'))
    if (value.get('chat')!=part.get('chat') or not value.get('allowed')
            or not 0<=time.time()-value.get('updated',0)<3):
        raise ValueError('Fast session cancelled before submission')


def execute_part(job,path,root,target_id,name,is_group,command,tree,selected,text_sender,sticker_sender,profile):
    index=command['index'];kind=command.get('kind')
    part_path=path.parent/(path.stem+'-part-'+str(index)+'.json')
    part={'operation':'send_'+str(kind),'chat':target_id,'created':command.get('created',0),
          'fast_session_nonce':path.stem,'state':'pending',**command.get('values',{})}
    # Wire metadata is never allowed to replace the session identity/state.
    part.update(operation='send_'+str(kind),chat=target_id,created=command.get('created',0),
                fast_session_nonce=path.stem,state='pending')
    before=dict(profile);started=time.monotonic()
    try:
        if kind not in ('text','sticker') or command.get('chat')!=target_id:
            raise ValueError('Invalid fast command destination or kind')
        if not 0<=time.time()-part['created']<5:raise ValueError('Fast command expired before submission')
        check_guard(part,part_path)
        if root is None:root=tree()
        if not selected(root,name,is_group):raise ValueError('Fast command destination changed')
        write(part_path,part)
        if kind=='text':
            root=text_sender(part,part_path,root,name,is_group,target_id)
        else:
            sticker_sender(part,part_path,root,name,is_group)
            # Escape closes the picker; never reuse its old tile coordinates.
            root=None
        if part.get('state')!='submitted':raise ValueError('Native action did not report submission')
    except Exception as exc:
        try:part=json.loads(part_path.read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
        crossed=part.get('state') in ('sending','submitted')
        part.update(state='uncertain' if crossed else 'failed_before_send',error=str(exc)[:200])
        if not crossed and ('cancelled' in str(exc) or 'expired' in str(exc)):part['cancelled']=True
        root=None
    part['ui_timing']={k:round(profile[k]-before.get(k,0),3) for k in profile}
    part['ui_seconds']=round(time.monotonic()-started,3)
    write(part_path,part)
    result={k:part[k] for k in ('state','error','cancelled','ui_timing','ui_seconds','favorite_seconds',
                               'quote_fallback','mention_fallback','mentions_applied') if k in part}
    job.setdefault('parts',{})[str(index)]=result;write(path,job)
    return root


def run_session(job,path,root,target_id,name,is_group,tree,selected,text_sender,sticker_sender,profile,window_signature):
    command_path=path.parent/(path.stem+'.command.json')
    expected=0;signature=window_signature()
    job.update(state='ready',parts={},ready_profile=dict(profile));write(path,job)
    # The caller retains the gateway GUI guard throughout this short lease.
    while time.time()-job['created']<25:
        try:command=json.loads(command_path.read_text(encoding='utf-8'))
        except (OSError,ValueError):time.sleep(.025);continue
        if command.get('close') is True:break
        index=command.get('index')
        if not isinstance(index,int) or index!=expected:
            time.sleep(.025);continue
        if expected>=6:break
        try:current_signature=window_signature()
        except Exception:current_signature=None
        if not current_signature or current_signature!=signature:root=None
        root=execute_part(job,path,root,target_id,name,is_group,command,tree,selected,text_sender,sticker_sender,profile)
        signature=current_signature
        expected+=1
    job.update(state='done',session_complete=True,completed_parts=expected);write(path,job)
