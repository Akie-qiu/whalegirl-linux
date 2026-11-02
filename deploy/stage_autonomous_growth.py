"""Merge only the growth delta into the current production tree."""
import base64
import hashlib
import json
from pathlib import Path
import re
import tarfile
from stage_tool_reply_guard import remote,merge_delta

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/autonomous-growth'
EXISTING={'whalegirl_linux/'+n for n in ('settings.py','memory_hub.py','memory.py','runtime.py',
    'proactive.py','app.py','web.html','console.js','token_usage.py','token-usage.js')}
NEW={'whalegirl_linux/memory_layers.py','whalegirl_linux/growth.py','whalegirl_linux/growth.js',
     'tests/test_autonomous_growth.py'}
FILES=EXISTING|NEW


def main():
    live=json.loads(remote("import base64,json\nfrom pathlib import Path\nr=Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(sorted(FILES))+
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))"))
    before={};after={};anchors={}
    for name in sorted(FILES):
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        if name in NEW:
            if raw is not None:raise RuntimeError('New source already exists: '+name)
            value=(ROOT/name).read_text(encoding='utf-8')
        else:
            if raw is None:raise RuntimeError('Live source missing: '+name)
            target=OUT/'server-before'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
            if name=='whalegirl_linux/web.html':
                value=raw.decode()
                for script in ('console','memory-hub','token-usage'):
                    pattern=r'/'+script+r'\.js\?v=[^"\s>]+'
                    if len(re.findall(pattern,value))!=1:raise RuntimeError('Script tag not unique: '+script)
                    value=re.sub(pattern,'/'+script+'.js?v=20261030-growth',value,count=1)
                pattern=r'(<script[^>]+src="/memory-hub\.js\?v=[^"]+"[^>]*></script>)'
                if len(re.findall(pattern,value))!=1 or '/growth.js' in value:raise RuntimeError('Growth script anchor changed')
                value=re.sub(pattern,r'\1<script defer src="/growth.js?v=20261030-growth"></script>',value,count=1)
                anchors[name]=['script_versions_and_growth_script_only']
            elif name=='whalegirl_linux/token-usage.js':
                value=raw.decode();anchor='{chat:r.model,search:'
                if value.count(anchor)!=1:raise RuntimeError('Review live usage label map')
                value=value.replace(anchor,"{growth_learn:'后台自主成长 · '+r.model,growth_search:'成长联网搜索',growth_fetch:'成长网页读取',chat:r.model,search:",1)
                anchors[name]=['growth_labels_in_existing_live_model_map']
            else:
                previous=(OUT/'local-before'/name).read_text(encoding='utf-8')
                value,anchors[name]=merge_delta(previous,(ROOT/name).read_text(encoding='utf-8'),raw.decode(),name)
        if name.endswith('.py'):compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(value,encoding='utf-8',newline='\n');after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':before,'after':after,
        'unique_line_anchors':anchors},indent=2),encoding='utf-8')
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in sorted(FILES):archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'files':len(FILES),'root':live['root'],'unique_line_anchors':anchors}))


if __name__=='__main__':main()
