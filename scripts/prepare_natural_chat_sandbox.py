"""Upload source-only tests to a fresh /tmp sandbox. No service or data writes."""
import base64
import io
import json
import subprocess
import tarfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/natural-chat-20261102'


def main():
    buf=io.BytesIO()
    with tarfile.open(fileobj=buf,mode='w:gz') as archive:
        for subdir in ('candidate-source','baseline-source'):
            for p in (OUT/subdir).rglob('*'):
                if p.is_file() and p.suffix in ('.py','.js','.html','.css','.yaml','.yml'):
                    archive.add(p,arcname=str(p.relative_to(OUT)).replace('\\','/'),recursive=False)
        for subdir in ('whalegirl_linux','styles','tests','deploy'):
            for p in (ROOT/subdir).rglob('*'):
                if p.is_file() and p.suffix in ('.py','.js','.html','.css','.yaml','.yml'):
                    archive.add(p,arcname='workspace-source/'+str(p.relative_to(ROOT)).replace('\\','/'),recursive=False)
        for source,name in [(ROOT/'scripts/replay_natural_chat.py','tools/replay_natural_chat.py'),
                             (ROOT/'artifacts/claude-review-20261102/replay/replay_warmth_eval.py','tools/harness.py')]:
            archive.add(source,arcname=name,recursive=False)
    payload=base64.b64encode(buf.getvalue()).decode()
    script='''import base64,io,json,pathlib,tarfile,tempfile
root=pathlib.Path(tempfile.mkdtemp(prefix='natural-chat-20261102-'))
with tarfile.open(fileobj=io.BytesIO(base64.b64decode(PAYLOAD)),mode='r:gz') as tar:
    for m in tar.getmembers():
        p=root/m.name
        if not m.isfile() or not p.resolve().is_relative_to(root):raise ValueError('Unexpected archive entry')
        p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(tar.extractfile(m).read())
print(json.dumps({'root':str(root),'bytes':SIZE,'source_only':True}))
'''.replace('PAYLOAD',repr(payload)).replace('SIZE',str(len(buf.getvalue())))
    result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','root@120.46.186.50',
                           '/opt/whalegirl-linux/venv/bin/python','-'],input=script,text=True,encoding='utf-8',
                          capture_output=True,timeout=90)
    if result.returncode:raise RuntimeError(result.stderr[-1200:])
    envelope=json.loads(result.stdout)
    (OUT/'sandbox.json').write_text(json.dumps(envelope,indent=2),'utf-8')
    print(json.dumps(envelope))


if __name__=='__main__':main()
