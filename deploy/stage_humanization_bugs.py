"""Capture read-only source baseline and prepare this task's core-only release."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import zlib

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/deploy-humanization-bugs-20261101'
TASKS = ('humanization-20261031', 'humanization-interactions-20261031', 'three-bugs-20261101')


def remote(source, timeout=60):
    result = subprocess.run(['ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        'root@120.46.186.50', 'python3', '-'], input=source, capture_output=True,
        text=True, encoding='utf-8', timeout=timeout)
    if result.returncode:
        raise RuntimeError('Read-only server check failed: ' + result.stderr[-1200:])
    raw=result.stdout.strip()
    return json.loads(zlib.decompress(base64.b64decode(raw[5:]))) if raw.startswith('ZLIB:') else json.loads(raw)


def normalized(value):
    return value.decode('utf-8-sig').replace('\r\n','\n').encode('utf-8')


def task_files():
    names, baselines = set(), {}
    for task in TASKS:
        folder = ROOT/'artifacts'/task
        raw = json.loads((folder/'change-manifest.json').read_text(encoding='utf-8-sig'))
        rows = raw['files'] if isinstance(raw, dict) else raw
        for row in rows:
            name = row['path']
            names.add(name)
            if name in baselines:
                continue
            if row.get('before_sha256') is None:
                baselines[name] = None
                continue
            candidates = (folder/'before'/name, folder/'before'/Path(name).name)
            path = next((p for p in candidates if p.is_file()), None)
            if not path:
                raise RuntimeError('Missing task source baseline: '+name)
            baselines[name] = path.read_bytes()
    # Validation fixtures are source only. No test data, credentials or logs.
    names.update(str(p.relative_to(ROOT)).replace('\\','/') for p in (ROOT/'tests').glob('*.py'))
    return names, baselines


def capture():
    OUT.mkdir(parents=True, exist_ok=True)
    packet = remote('''
import base64,hashlib,json,pathlib,shutil,sqlite3,subprocess,urllib.request,zlib
root=pathlib.Path('/opt/whalegirl-linux/current').resolve();data=pathlib.Path('/var/lib/whalegirl-linux/core')
def cmd(*args):return subprocess.check_output(args,text=True,timeout=15).strip()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def api(route):
 req=urllib.request.Request('http://127.0.0.1:8092/'+route,headers={'Authorization':'Bearer '+(data/'admin.token').read_text().strip()})
 with opener.open(req,timeout=12) as res:return json.load(res)
state=api('api/state');health=api('health');config=api('api/config')
files={}
for folder in ('whalegirl_linux','tests'):
 for p in (root/folder).rglob('*'):
  if p.is_file() and p.suffix in ('.py','.js','.css','.html'):
   files[str(p.relative_to(root))]=base64.b64encode(p.read_bytes()).decode()
for p in root.glob('*.md'):files[p.name]=base64.b64encode(p.read_bytes()).decode()
db=sqlite3.connect((data/'state.sqlite3').as_uri()+'?mode=ro',uri=True)
inflight={t:db.execute(q).fetchone()[0] for t,q in {
 'outbound':"SELECT count(*) FROM outbound_actions WHERE state='sending'",
 'pictures':"SELECT count(*) FROM picture_jobs WHERE state IN ('generating','sending')",
 'moments':"SELECT count(*) FROM moments_actions WHERE state='sending'"}.items()}
db.close()
free=shutil.disk_usage(data).free
protected={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in data.iterdir()
 if p.is_file() and (p.suffix in ('.enc','.key','.token') or p.name in ('settings.json','model-profiles.json','persona-cards.json'))}
emit=lambda value:print('ZLIB:'+base64.b64encode(zlib.compress(json.dumps(value).encode(),6)).decode())
emit({'root':str(root),'files':files,'protected_hashes':protected,
 'health':health,'state':{'wechat_status':state.get('gateway',{}).get('status'),'enabled':state.get('enabled'),
  'paused':bool(state.get('paused_reason')),'processing':bool(state.get('processing'))},
 'config':{k:config.get(k) for k in ('autonomy_enabled','experience_growth_enabled','growth_enabled','tieba_enabled','growth_tieba_enabled','model','reasoning_effort','max_tokens','context_messages')},
 'inflight':inflight,'disk_free_bytes':free,'database_bytes':(data/'state.sqlite3').stat().st_size,
 'native_pid':cmd('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat'),
 'reader_pid':cmd('systemctl','show','whalegirl-native-reader.service','-p','MainPID','--value'),
 'container':cmd('docker','inspect','--format','{{.Id}} {{.RestartCount}}','whalegirl-wechat-candidate-4118'),
 'service_limits':cmd('systemctl','show','whalegirl-linux.service','-p','MemoryMax','-p','MemoryHigh','-p','CPUQuotaPerSecUSec')})
''')
    for name, encoded in packet.pop('files').items():
        target = OUT/'server-before'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(encoded))
    (OUT/'preflight.json').write_text(json.dumps(packet, ensure_ascii=False, indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False))


def prepare():
    packet=json.loads((OUT/'preflight.json').read_text(encoding='utf-8'))
    names, baselines = task_files()
    results, before, after = [], {}, {}
    for name in sorted(names):
        local=ROOT/name
        if not local.is_file():
            raise RuntimeError('Missing local release file: '+name)
        saved=OUT/'server-before'/name
        old=saved.read_bytes() if saved.exists() else None
        content=normalized(local.read_bytes())
        status='identical'
        if old is not None and normalized(old)==content:
            continue
        if name in baselines:
            base=baselines[name]
            if base is None:
                if old is not None:
                    raise RuntimeError('New task file already exists with different contents: '+name)
                status='new'
            elif old is not None and normalized(old)==normalized(base):
                status='task_delta'
            elif old is None:
                raise RuntimeError('Previously existing source missing on server: '+name)
            else:
                if not name.endswith(('.py','.js','.html','.md')):
                    raise RuntimeError('Cannot merge source: '+name)
                merge=OUT/'merge'/name
                merge.parent.mkdir(parents=True,exist_ok=True)
                ours=merge.with_suffix(merge.suffix+'.local')
                ancestor=merge.with_suffix(merge.suffix+'.base')
                theirs=merge.with_suffix(merge.suffix+'.server')
                ours.write_bytes(content);ancestor.write_bytes(normalized(base));theirs.write_bytes(normalized(old))
                result=subprocess.run(['git','merge-file','-p',str(ours),str(ancestor),str(theirs)],capture_output=True)
                merge.write_bytes(result.stdout)
                if result.returncode:
                    resolved=OUT/'resolved'/name
                    if not resolved.is_file():
                        results.append({'path':name,'status':'conflict','lines':len(result.stdout.splitlines())})
                        continue
                    content=normalized(resolved.read_bytes())
                    if b'<<<<<<<' in content or b'>>>>>>>' in content:
                        raise RuntimeError('Unresolved merge markers: '+name)
                    status='reviewed_merge'
                else:
                    content=result.stdout;status='merged_server_delta'
        elif name.startswith('tests/'):
            resolved=OUT/'resolved'/name
            if old is not None and resolved.is_file():
                content=normalized(resolved.read_bytes());status='reviewed_test_fixture'
            elif old is not None:
                continue  # Keep the server's tests for untouched feature versions.
            else:
                status='test_fixture'
        else:
            raise RuntimeError('Unscoped release source: '+name)
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
        before[name]=hashlib.sha256(old).hexdigest() if old is not None else None
        after[name]=hashlib.sha256(content).hexdigest()
        results.append({'path':name,'status':status})
    (OUT/'merge-review.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    conflicts=[r for r in results if r['status']=='conflict']
    print(json.dumps({'changed':len(results),'conflicts':conflicts,'source_changes':[
        r for r in results if r['path'].startswith('whalegirl_linux/')]},ensure_ascii=False))
    if conflicts:
        raise RuntimeError('Review conflicting server changes before deployment')
    (OUT/'manifest.json').write_text(json.dumps({'root':packet['root'],'before':before,'after':after},indent=2),encoding='utf-8')


if __name__=='__main__':
    import sys
    if '--capture' in sys.argv:capture()
    if '--prepare' in sys.argv:prepare()
