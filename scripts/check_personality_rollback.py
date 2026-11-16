"""Offline rollback smoke test against an independent old source copy.

Never opens production data or a network connection. With --candidate-data,
the caller must supply an already offline data snapshot; SQLite's backup API
copies it to the new output directory. The source is opened read-only.
No files, chats, memories, or database tables are deleted.
"""
import argparse
import asyncio
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time


ROOT=Path(__file__).resolve().parents[1]
PLUGIN_SETTINGS=('life_autonomy_enabled','growth_mature_traits_chat_enabled')
PROTECTED_TABLES=('inbox','message_log','history','memories','memory_revisions',
    'growth_personality_evidence','growth_personality_states','growth_personality_revisions',
    'life_parameter_changes','life_parameter_events','life_parameter_heads')


def table_fingerprints(path):
    result={}
    with sqlite3.connect(path) as db:
        tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name in PROTECTED_TABLES:
            if name not in tables:continue
            digest=hashlib.sha256();count=0
            for row in db.execute('SELECT * FROM "'+name+'" ORDER BY rowid'):
                digest.update(repr(row).encode('utf-8'));digest.update(b'\n');count+=1
            result[name]={'rows':count,'sha256':digest.hexdigest()}
    return result


def export_baseline(destination, ref):
    revision=subprocess.run(['git','rev-parse','--verify',ref+'^{commit}'],cwd=ROOT,
        check=True,capture_output=True,text=True).stdout.strip()
    archive=subprocess.run(['git','archive','--format=tar',revision],cwd=ROOT,
        check=True,capture_output=True).stdout
    destination.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        for entry in bundle:
            target=(destination/entry.name).resolve()
            if not target.is_relative_to(destination.resolve()):raise ValueError('Unsafe git archive path')
            if entry.isdir():target.mkdir(parents=True,exist_ok=True)
            elif entry.isfile():
                target.parent.mkdir(parents=True,exist_ok=True)
                with bundle.extractfile(entry) as source,target.open('wb') as output:
                    shutil.copyfileobj(source,output)
            else:raise ValueError('Unsupported git archive entry')
    return revision


def copy_source_baseline(source,destination):
    source=source.resolve(strict=True)
    if not (source/'whalegirl_linux'/'runtime.py').is_file():raise ValueError('Baseline has no Runtime source')
    if not (source/'styles').is_dir():raise ValueError('Baseline has no character styles')
    destination.mkdir(mode=0o700)
    for name in ('whalegirl_linux','styles'):
        shutil.copytree(source/name,destination/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    digest=hashlib.sha256()
    for path in sorted(destination.rglob('*')):
        if path.is_file():
            digest.update(path.relative_to(destination).as_posix().encode('utf-8'));digest.update(b'\0')
            digest.update(path.read_bytes());digest.update(b'\0')
    return digest.hexdigest()


def copy_offline_snapshot(source,destination):
    source=source.resolve(strict=True)
    if not source.is_dir():raise ValueError('The offline snapshot must be a directory')
    destination.mkdir(mode=0o700)
    # Config requires its local key material even in an offline constructor.
    # Nothing in this directory is printed or included in the public report.
    names=('settings.json','encryption.key','model-profiles.json','model-api-keys.enc',
        'api-key.enc','image-api-key.enc','search-api-key.enc','admin.token','gateway.token',
        'persona-cards.json','bilibili-session.enc')
    for name in names:
        path=source/name
        if path.is_file():shutil.copy2(path,destination/name)
    if not (destination/'settings.json').is_file():raise ValueError('Snapshot has no settings.json')
    uri=(source/'state.sqlite3').as_uri()+'?mode=ro'
    with sqlite3.connect(uri,uri=True) as original,sqlite3.connect(destination/'state.sqlite3') as copy:
        original.backup(copy)


async def create_synthetic_snapshot(destination):
    sys.path.insert(0,str(ROOT))
    from loguru import logger
    logger.remove()
    from whalegirl_linux.settings import Config,Settings
    from whalegirl_linux.store import Store
    from whalegirl_linux.runtime import Runtime

    class OfflineGateway:
        async def close(self):pass

    destination.mkdir(mode=0o700)
    config=Config(destination)
    account='rollback-synthetic';chat='synthetic@chatroom';now=time.time()
    config.save(Settings(enabled=False,account_id=account,allowed_chat_ids=[chat],
        life_autonomy_enabled=True,growth_mature_traits_chat_enabled=True),'synthetic-offline-key')
    store=Store(destination/'state.sqlite3')
    runtime=Runtime(config,store,OfflineGateway(),ROOT/'styles')
    try:
        key=f'{account}/{chat}/synthetic-1'
        event={'key':key,'account':account,'chat':chat,'sender':'合成说话人','sender_id':'synthetic-person',
            'content':'合成消息：记住我喜欢音乐，只用于离线兼容验证。','timestamp':now,
            'kind':1,'is_self':0,'local_id':1,'trigger':'direct'}
        store.insert(event,state='ignored');store.observe([event])
        memory=runtime.memory_book.change(account,[chat],{'chat':chat,'subject':'synthetic-person',
            'kind':'preference','content':'合成偏好：喜欢音乐，仅用于离线兼容验证','source_key':key})
        runtime.life_autonomy.propose_and_apply('growth_exploration',config.settings.growth_exploration+1,
            '合成生活事务：验证旧发布不会删除新历史',actor='admin')
        role=runtime.memory_hub.role();trait='interest:synthetic-music'
        with store.db:
            store.db.execute('INSERT INTO history(account,chat,role,content) VALUES(?,?,?,?)',
                (account,chat,'user',event['content']))
            store.db.execute('''INSERT INTO growth_personality_evidence
                (account,role_id,trait_key,category,topic,memory_id,journal_id,source_id,
                 source_kind,stance,excerpt_hash,excerpt,created,delta)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (account,role,trait,'interest','合成音乐',memory['id'],'synthetic-journal','synthetic-source',
                 'bilibili','support','synthetic-hash','合成证据，不代表真实阅读',now,1))
            state={'category':'interest','topic':'合成音乐','status':'candidate'}
            store.db.execute('''INSERT INTO growth_personality_states
                (account,role_id,trait_key,category,topic,status,support_count,challenge_count,
                 source_count,first_seen,last_seen,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                (account,role,trait,'interest','合成音乐','candidate',1,0,1,now,now,now))
            store.db.execute('''INSERT INTO growth_personality_revisions
                (account,role_id,trait_key,before_json,after_json,reason,created) VALUES(?,?,?,?,?,?,?)''',
                (account,role,trait,'{}',json.dumps(state,ensure_ascii=False),'合成兼容验证',now))
    finally:await runtime.close()


WORKER=r'''
import asyncio,json,socket,sys
from pathlib import Path
root=Path(sys.argv[1]);data=Path(sys.argv[2]);sys.path.insert(0,str(root))
def no_network(*args,**kwargs):raise RuntimeError('Offline check forbids network')
from whalegirl_linux.settings import Config
from whalegirl_linux.store import Store
from whalegirl_linux.runtime import Runtime
class Gateway:
    async def status(self):return {'status':'logged_out'}
    async def close(self):pass
def no_model(*args,**kwargs):raise RuntimeError('Offline check forbids model calls')
async def main():
    # asyncio itself uses a local socket pair while creating its Windows loop.
    # Install the transport guard after loop creation, before loading Config.
    socket.socket.connect=no_network;socket.socket.connect_ex=no_network
    config=Config(data);values=config.settings.model_dump()
    for key,value in list(values.items()):
        if key.endswith('_enabled') and isinstance(value,bool):values[key]=False
    values['enabled']=False
    config.settings=type(config.settings).model_validate(values)
    runtime=Runtime(config,Store(data/'state.sqlite3'),Gateway(),root/'styles',engine_factory=no_model)
    try:
        await runtime.start();await asyncio.sleep(0)
        failures=[task for task in runtime.tasks if task.done() and not task.cancelled() and task.exception()]
        if failures:raise RuntimeError('Old background task failed during startup')
        print(json.dumps({'runtime_initialized':True,'background_tasks_started':len(runtime.tasks),
            'external_integrations_disabled_in_memory':True,'network_blocked':True,'model_calls':0}))
    finally:await runtime.close()
try:asyncio.run(main())
except Exception as exc:
    result={'runtime_initialized':False,'error_type':type(exc).__name__}
    if hasattr(exc,'errors'):
        result['unsupported_settings']=[str(error.get('loc',('unknown',))[0])
            for error in exc.errors(include_input=False,include_url=False) if error.get('type')=='extra_forbidden']
    print(json.dumps(result));sys.exit(1)
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-data',type=Path,help='Already offline snapshot; original is opened read-only')
    parser.add_argument('--baseline-ref',default='HEAD',help='Baseline git commit/ref, default HEAD')
    parser.add_argument('--baseline-source',type=Path,help='Actual old release source snapshot; takes precedence over git ref')
    parser.add_argument('--output',type=Path,help='A new output directory; it must not exist')
    args=parser.parse_args()
    output=(args.output or ROOT/'test-runs'/('personality-rollback-'+str(time.time_ns()))).resolve()
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    baseline=output/'old-source';data=output/'offline-data'
    if args.baseline_source:
        revision=None;source_hash=copy_source_baseline(args.baseline_source,baseline)
    else:revision=export_baseline(baseline,args.baseline_ref);source_hash=None
    if args.candidate_data:copy_offline_snapshot(args.candidate_data,data)
    else:asyncio.run(create_synthetic_snapshot(data))
    settings_path=data/'settings.json';before=settings_path.read_bytes()
    (output/'candidate-settings.private.json').write_bytes(before)
    values=json.loads(before);removed=[key for key in PLUGIN_SETTINGS if key in values]
    for key in removed:values.pop(key)
    settings_path.write_text(json.dumps(values,ensure_ascii=False,indent=2),encoding='utf-8')
    protected_before=table_fingerprints(data/'state.sqlite3')
    worker=subprocess.run([sys.executable,'-X','utf8','-I','-c',WORKER,str(baseline),str(data)],
        cwd=output,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
    protected_after=table_fingerprints(data/'state.sqlite3')
    lines=[line for line in worker.stdout.splitlines() if line.startswith('{')]
    outcome=json.loads(lines[-1]) if lines else {'runtime_initialized':False,'error_type':'NoWorkerResult'}
    report={'kind':'personality-life-offline-rollback','baseline_revision':revision,
        'baseline_kind':'actual_source_snapshot' if args.baseline_source else 'git_export',
        'baseline_source_sha256':source_hash,
        'data_kind':'offline_snapshot_copy' if args.candidate_data else 'synthetic',
        'removed_plugin_settings':removed,'other_settings_preserved':all(values[key]==json.loads(before)[key] for key in values),
        'old_runtime':outcome,'worker_exit_code':worker.returncode,
        'protected_tables':protected_before,'protected_data_unchanged':protected_before==protected_after,
        'database_restored':False,'network_used':False,'production_touched':False,
        'limitations':['Offline startup smoke only; no real WeChat or Bilibili transport was exercised',
            'Abrupt process termination between JSON settings and SQLite commits is not simulated']}
    report['passed']=worker.returncode==0 and outcome.get('runtime_initialized',False) and report['protected_data_unchanged']
    report_path=output/'report.json';report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(report_path),'passed':report['passed'],'data_kind':report['data_kind'],
        'old_runtime_started':outcome.get('runtime_initialized',False),
        'protected_data_unchanged':report['protected_data_unchanged']},ensure_ascii=False))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
