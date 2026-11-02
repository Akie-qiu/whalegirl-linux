"""Merge this latency followup onto production without unrelated local work."""
import base64
import hashlib
import json
from pathlib import Path
import re
import tarfile
from stage_tool_reply_guard import remote,merge_delta

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'artifacts/reply-pipeline'
NEW={'whalegirl_linux/reply_pipeline.py','tests/test_reply_pipeline.py'}
FILES=NEW|{'deploy/native_fast_turn_v1.py','tests/test_fast_send.py'}|{'whalegirl_linux/'+n for n in
    ('runtime.py','app.py','proactive.js','console.js','web.html')}


def main():
    live=json.loads(remote("import pathlib,base64,json\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(sorted(FILES))+
        "\nhelper=pathlib.Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools/native_fast_turn_v1.py')\n"
        "print(json.dumps({'root':str(r),'native_helper':base64.b64encode(helper.read_bytes()).decode(),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))"))
    before={};after={};reviewed={}
    for name in sorted(FILES):
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        if name in NEW:
            if raw is not None:raise RuntimeError('New file already present: '+name)
            value=(ROOT/name).read_text(encoding='utf-8')
        else:
            saved=OUT/'server-before'/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(raw)
            production=raw.decode('utf-8')
            if name.endswith('web.html'):
                value=production
                for asset in ('console','proactive'):
                    value,count=re.subn(r'(/'+asset+r'\.js\?v=)[^"\s]+',r'\g<1>20261030-reply-pipeline',value)
                    if count!=1:raise RuntimeError('Asset version changed: '+asset)
            else:
                baseline=(ROOT/'artifacts/fast-turn/payload'/name if name=='tests/test_fast_send.py'
                          else OUT/'local-before'/name)
                value,reviewed[name]=merge_delta(baseline.read_text(encoding='utf-8'),
                    (ROOT/name).read_text(encoding='utf-8'),production,name)
        if name.endswith('.py'):compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(value,encoding='utf-8',newline='\n');after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    helper=base64.b64decode(live['native_helper'])
    if hashlib.sha256(helper).hexdigest()!=before['deploy/native_fast_turn_v1.py']:
        raise RuntimeError('Native helper differs from the current release')
    manifest={'root':live['root'],'before':before,'after':after,
              'native_before':hashlib.sha256(helper).hexdigest(),'unique_line_anchors':reviewed}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in sorted(FILES):archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'files':len(FILES),'root':live['root'],'unique_line_anchors':reviewed}))


if __name__=='__main__':main()
