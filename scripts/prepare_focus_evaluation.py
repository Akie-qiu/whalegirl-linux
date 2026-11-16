"""Stage exact server baseline plus local changes; no live files/services changed."""
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import time

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts'/'focus-evaluation-20261105'
HOST='root@120.46.186.50'
PYTHON='/opt/whalegirl-linux/venv/bin/python'


def remote(code,timeout=45):
    result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',HOST,PYTHON,'-'],
        input=code,capture_output=True,text=True,encoding='utf-8',timeout=timeout)
    if result.returncode:raise RuntimeError('read_only_or_sandbox_operation_failed: '+result.stderr[-500:])
    return json.loads(result.stdout)


def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    packet=remote('''import base64,hashlib,io,json,pathlib,tarfile
root=pathlib.Path('/opt/whalegirl-linux/current').resolve();buf=io.BytesIO();hashes={}
with tarfile.open(fileobj=buf,mode='w:gz') as tar:
 for name in ('whalegirl_linux','styles'):
  for p in sorted((root/name).rglob('*')):
   if p.is_file() and not p.is_symlink() and p.suffix in ('.py','.js','.html','.css','.yaml','.yml','.json') and '__pycache__' not in p.parts and 'vendor' not in p.parts and '.before-' not in p.name:
    rel=str(p.relative_to(root));tar.add(p,arcname=rel,recursive=False);hashes[rel]=hashlib.sha256(p.read_bytes().replace(b'\\r\\n',b'\\n')).hexdigest()
print(json.dumps({'release':str(root),'source':base64.b64encode(buf.getvalue()).decode(),'hashes':hashes}))
''')
    raw=base64.b64decode(packet.pop('source'))
    if len(raw)>8*1024*1024:raise RuntimeError('source_archive_unexpectedly_large')
    baseline=OUT/'baseline';candidate=OUT/'candidate'
    for folder in (baseline,candidate):
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as tar:
            for member in tar.getmembers():
                path=folder/member.name
                if not member.isfile() or not path.resolve().is_relative_to(folder.resolve()):raise RuntimeError('invalid_source_archive')
                path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(tar.extractfile(member).read())
    merges=[]
    for local in sorted((ROOT/'whalegirl_linux').rglob('*')):
        if not local.is_file() or local.suffix not in ('.py','.js','.html','.css') or any(part in local.parts for part in ('__pycache__','vendor')):continue
        rel=local.relative_to(ROOT);target=candidate/rel
        before=subprocess.run(['git','show','HEAD:'+rel.as_posix()],cwd=ROOT,capture_output=True)
        original=before.stdout.replace(b'\r\n',b'\n') if before.returncode==0 else None
        changed=local.read_bytes().replace(b'\r\n',b'\n')
        if changed==original:continue
        server=target.read_bytes().replace(b'\r\n',b'\n') if target.exists() else None
        result=changed
        if server is not None and original is not None and server!=original:
            inputs=OUT/'merge-inputs'/local.name;inputs.mkdir(parents=True,exist_ok=True)
            paths=[inputs/name for name in ('local','common','server')]
            for path,content in zip(paths,(changed,original,server)):path.write_bytes(content)
            merged=subprocess.run(['git','merge-file','-p',*[str(path) for path in paths]],capture_output=True)
            if merged.returncode:raise RuntimeError('source_merge_conflict: '+str(rel))
            result=merged.stdout;method='three_way'
        else:method='local_change'
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(result)
        merges.append({'file':rel.as_posix(),'method':method,'sha256':hashlib.sha256(result).hexdigest()})
    buf=io.BytesIO()
    with tarfile.open(fileobj=buf,mode='w:gz') as tar:
        for folder in (baseline,candidate):
            for path in sorted(folder.rglob('*')):
                if path.is_file():tar.add(path,arcname=str(path.relative_to(OUT)).replace('\\','/'),recursive=False)
        tar.add(ROOT/'scripts/evaluate_focus_chat.py',arcname='evaluate_focus_chat.py',recursive=False)
    payload=base64.b64encode(buf.getvalue()).decode()
    remote_stage=remote('''import base64,io,json,pathlib,tarfile,tempfile,os,shutil
if shutil.disk_usage('/tmp').free<800*1024*1024:raise RuntimeError('disk_guard')
root=pathlib.Path(tempfile.mkdtemp(prefix='whalegirl-focus-eval-20261105-'));os.chmod(root,0o700)
with tarfile.open(fileobj=io.BytesIO(base64.b64decode(PAYLOAD)),mode='r:gz') as tar:
 for member in tar.getmembers():
  path=root/member.name
  if not member.isfile() or not path.resolve().is_relative_to(root):raise RuntimeError('invalid_source_archive')
  path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(tar.extractfile(member).read());path.chmod(0o600)
print(json.dumps({'root':str(root),'bytes':SIZE}))
'''.replace('PAYLOAD',repr(payload)).replace('SIZE',str(len(buf.getvalue()))))
    meta={**packet,**remote_stage,'merges':merges,'local_source':str(candidate),'at':time.time()}
    (OUT/'stage.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({'baseline_release':meta['release'],'test_root':meta['root'],'changed_files':len(merges),
        'archive_bytes':meta['bytes'],'production_writes':False,'out':str(OUT)},ensure_ascii=True))


if __name__=='__main__':prepare()
