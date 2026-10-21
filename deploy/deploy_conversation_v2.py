"""Stage focused checks, then upgrade the core/reader while keeping native WeChat running."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import pwd
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request

BASE=Path('/opt/whalegirl-linux');ASSETS=Path('/opt/whalegirl-linux-assets')
DATA=Path('/var/lib/whalegirl-linux/core')
NATIVE=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
CONTAINER='whalegirl-wechat-candidate-4118'
CODE=['settings.py','store.py','memory.py','context.py','conversation.py','stickers.py','runtime.py',
      'gateway.py','visible_media.py','media_locator.py','app.py','console.js','console.css','habits.js','improvements.js','web.html']
EXPECTED={'whalegirl_linux/'+n for n in CODE}|{'deploy/native_message_reader.py','deploy/chat_select_linux.py',
    'tests/test_conversation_upgrade.py','deploy/deploy_conversation_v2.py'}
REPORT=ASSETS/'conversation-v2-stage.json'
account=pwd.getpwnam('whalegirl-ai')
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
token=(DATA/'admin.token').read_text().strip()


def api(route,body=None):
    req=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with opener.open(req,timeout=10) as response:return json.load(response)


def command(*args):
    return subprocess.run(list(args),check=True,capture_output=True,text=True,timeout=25).stdout


def stage_release():
    stamp=time.strftime('%Y%m%d-%H%M%S');previous=(BASE/'current').resolve()
    stage=BASE/'releases'/('conversation-v2-'+stamp);backup=ASSETS/('conversation-v2-before-'+stamp)
    backup.mkdir(mode=0o700)
    with tarfile.open(ASSETS/'conversation-v2-20261021.tar') as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=EXPECTED or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected archive entries')
        contents={m.name:archive.extractfile(m).read() for m in members}
    for name,content in contents.items():
        if name.endswith('.py'):compile(content,name,'exec')
    shutil.copytree(previous,stage,symlinks=True)
    for name,content in contents.items():
        target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
    test=subprocess.run([str(BASE/'venv/bin/python'),'-m','unittest','discover','-s','tests',
                         '-p','test_conversation_upgrade.py','-v'],cwd=stage,capture_output=True,text=True,timeout=35)
    (backup/'focused-checks.log').write_text(test.stdout+test.stderr)
    print((test.stdout+test.stderr)[-6000:],flush=True)
    if test.returncode:raise RuntimeError('Focused checks failed; live installation unchanged')
    # Changed memory/send paths also retain their existing attribution/uncertain-send regressions.
    if (stage/'whalegirl_linux/runtime.py').read_bytes()!=(previous/'whalegirl_linux/runtime.py').read_bytes():
        test=subprocess.run([str(BASE/'venv/bin/python'),'-m','unittest','discover','-s','tests',
                             '-p','test_chat_habits.py','-v'],cwd=stage,capture_output=True,text=True,timeout=35)
        (backup/'existing-memory-send-checks.log').write_text(test.stdout+test.stderr)
        print((test.stdout+test.stderr)[-3500:],flush=True)
        if test.returncode:raise RuntimeError('Existing memory/send checks failed; live installation unchanged')
    for target in [stage,*stage.rglob('*')]:
        if not target.is_symlink():os.chown(target,0,account.pw_gid);target.chmod(0o750 if target.is_dir() else 0o640)
    report={'stage':str(stage),'previous':str(previous),'backup':str(backup),'stamp':stamp,
            'checks':'passed','hashes':{n:hashlib.sha256(c).hexdigest() for n,c in contents.items()}}
    REPORT.write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)


def activate():
    report=json.loads(REPORT.read_text());stage=Path(report['stage']);previous=Path(report['previous'])
    backup=Path(report['backup']);stamp=report['stamp'];current=BASE/'current'
    if current.resolve()!=previous:raise RuntimeError('Current release changed after staging')
    original=api('config');original.pop('api_key_configured',None)
    state=api('state')
    if state.get('paused_reason') or state.get('counts',{}).get('uncertain'):raise RuntimeError('Resolve pending send before upgrade')
    native_before=command('docker','exec',CONTAINER,'pgrep','-x','wechat').strip()
    (backup/'settings.json').write_text(json.dumps(original,ensure_ascii=False,indent=2));os.chmod(backup/'settings.json',0o600)
    print('Pausing new replies and draining existing sends',flush=True)
    api('config',{'settings':{**original,'enabled':False}})
    stopped=False;native_changed=False
    try:
        for _ in range(150):
            state=api('state')
            if not state.get('processing'):break
            time.sleep(.2)
        else:raise RuntimeError('Active operation did not finish')
        if state.get('counts',{}).get('uncertain'):raise RuntimeError('Pending uncertain send')
        command('systemctl','stop','whalegirl-linux');stopped=True
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
            with sqlite3.connect(backup/'state.sqlite3') as saved:db.backup(saved)
        native_files=('native_message_reader.py','chat_select_linux.py','media_locator.py')
        native_changed=any(not (NATIVE/name).is_file() or
                           (NATIVE/name).read_bytes()!=(stage/('deploy' if name!='media_locator.py' else 'whalegirl_linux')/name).read_bytes()
                           for name in native_files)
        if native_changed:
            for name in native_files:
                if (NATIVE/name).is_file():shutil.copy2(NATIVE/name,backup/('native-'+name))
            command('systemctl','stop','whalegirl-native-reader')
            for name in native_files:
                target=NATIVE/name
                source=stage/('deploy' if name!='media_locator.py' else 'whalegirl_linux')/name
                target.write_bytes(source.read_bytes());os.chown(target,0,account.pw_gid);target.chmod(0o640)
        pending=BASE/('current.conversation-v2-'+stamp);pending.symlink_to(stage,target_is_directory=True);os.replace(pending,current)
        # Use the new store only while the core is stopped. Preserve all historical sources/revisions.
        sys.path.insert(0,str(stage))
        from whalegirl_linux.store import Store
        from whalegirl_linux.memory import MemoryBook,own_text
        from whalegirl_linux.settings import Settings
        store=Store(DATA/'state.sqlite3');book=MemoryBook(store);migrated=0
        for row in store.db.execute('SELECT * FROM memories').fetchall():
            value=dict(row)
            source=store.db.execute('SELECT * FROM message_log WHERE key=?',(value['source_key'],)).fetchone()
            if (value.get('scope')!='profile' and not value['archived'] and value['kind'] in ('fact','preference') and source and
                source['sender_id']==value['subject'] and own_text(dict(source)) and
                not any(w in value['content'] for w in ('本群','群里','今天','昨天','刚才'))):
                # Existing memories were inspected as attributed names/aliases, retaining their source chat.
                book.change(original['account_id'],original['allowed_chat_ids'],
                    {'op':'update','id':value['id'],'version':value['version'],'scope':'profile'},actor='admin')
                migrated+=1
            else:book.archive_snapshot(value)
        store.close()
        defaults={'memory_scope_mode':'shared_profiles','global_memory':True,'context_messages':28,
            'history_recall_limit':6,'memory_recall_limit':8,'context_char_budget':14000,
            'memory_auto_capture':True,'background_digest_enabled':True,'colloquialism':'high','teasing':'normal',
            'persona_density':'low','followup_questions':'low','reply_length':'short'}
        updated=Settings.model_validate({**defaults,**original,'enabled':False})
        settings_file=DATA/'settings.json';settings_file.write_text(updated.model_dump_json(indent=2));os.chmod(settings_file,0o600)
        # Root-generated migration projections must remain owned by the service account.
        archive=DATA/'memory-archive'
        if archive.exists():
            for path in [archive,*archive.rglob('*')]:os.chown(path,account.pw_uid,account.pw_gid);path.chmod(0o700 if path.is_dir() else 0o600)
        if native_changed:command('systemctl','start','whalegirl-native-reader')
        command('systemctl','start','whalegirl-linux')
        for _ in range(60):
            try:
                state=api('state')
                if state['gateway']['status']=='logged_in':break
            except (OSError,ValueError):pass
            time.sleep(.25)
        else:raise RuntimeError('New core unavailable or login changed')
        live=api('config');live.pop('api_key_configured',None);live['enabled']=original['enabled']
        api('config',{'settings':live})
        inbox=api('messages?limit=5');memories=api('memory');state=api('state')
        if len(live['allowed_chat_ids'])!=len(original['allowed_chat_ids']) or live['trigger_only_chat_ids']!=original['trigger_only_chat_ids']:
            raise RuntimeError('Conversation authorization changed')
        native_after=command('docker','exec',CONTAINER,'pgrep','-x','wechat').strip()
        result={'release':str(stage),'backup':str(backup),'checks':'passed','native_pid_unchanged':native_before==native_after,
                'native_pid':native_after,'gateway_status':state['gateway']['status'],'enabled':state['enabled'],
                'authorized_chats':len(live['allowed_chat_ids']),'scope_mode':live['memory_scope_mode'],
                'migrated_profiles':migrated,'inbox_api_rows':len(inbox['messages']),'memory_api_rows':len(memories['memories']),
                'paused':state.get('paused_reason'),'last_error':state.get('last_error')}
        (ASSETS/'conversation-v2-deployment.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False),flush=True)
    except BaseException:
        if stopped:
            command('systemctl','stop','whalegirl-linux')
            rollback=BASE/('current.conversation-v2-rollback-'+stamp);rollback.symlink_to(previous,target_is_directory=True);os.replace(rollback,current)
        if native_changed:
            command('systemctl','stop','whalegirl-native-reader')
            for name in ('native_message_reader.py','chat_select_linux.py','media_locator.py'):
                original_file=backup/('native-'+name)
                if original_file.is_file():(NATIVE/name).write_bytes(original_file.read_bytes())
            command('systemctl','start','whalegirl-native-reader')
        if stopped:
            (DATA/'settings.json').write_text(json.dumps(original,ensure_ascii=False,indent=2));os.chmod(DATA/'settings.json',0o600)
            command('systemctl','start','whalegirl-linux')
        else:api('config',{'settings':original})
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('operation',choices=['stage','activate']);args=parser.parse_args()
    stage_release() if args.operation=='stage' else activate()
