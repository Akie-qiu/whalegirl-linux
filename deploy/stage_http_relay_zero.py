"""Ship only HTTP validation, model error diagnostics and zero pacing controls."""
import base64
import hashlib
import json
from pathlib import Path
import re
import tarfile
from stage_tool_reply_guard import remote,merge_delta

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'artifacts/http-relay-zero'
NEW={'tests/test_http_relay.py'}
FILES=NEW|{'whalegirl_linux/'+n for n in ('settings.py','upstream/ai_engine.py','console.js','web.html')}


def main():
    live=json.loads(remote("import base64,json\nfrom pathlib import Path\nr=Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(sorted(FILES))+
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))"))
    before={};after={}
    for name in sorted(FILES):
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        if name in NEW:
            if raw is not None:raise RuntimeError('New test already present: '+name)
            value=(ROOT/name).read_text(encoding='utf-8')
        else:
            saved=OUT/'server-before'/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(raw)
            if name.endswith('web.html'):
                value=raw.decode()
                for a,b in [('id="cooldown" type="number" min="5"','id="cooldown" type="number" min="0"'),
                            ('id="send-interval" type="number" min="3"','id="send-interval" type="number" min="0"'),
                            ('<span class="hint">先生成，再等待发送间隔</span>','<span class="hint">0 表示取消额外间隔；先生成，再按设置发送</span>')]:
                    if value.count(a)!=1:raise RuntimeError('Timing UI anchor changed')
                    value=value.replace(a,b,1)
                value,count=re.subn(r'(/console\.js\?v=)[^"\s]+',r'\g<1>20261030-http-relay',value)
                if count!=1:raise RuntimeError('Console asset version anchor changed')
            else:
                value,_=merge_delta((OUT/'local-before'/name).read_text(encoding='utf-8'),
                    (ROOT/name).read_text(encoding='utf-8'),raw.decode(),name)
        if name.endswith('.py'):compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(value,encoding='utf-8',newline='\n');after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':before,'after':after},indent=2))
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in sorted(FILES):archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'files':len(FILES),'root':live['root']}))


if __name__=='__main__':main()
