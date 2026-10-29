"""Merge cache-only deltas; keep all live chat/Tieba behavior and source data."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
from stage_tool_reply_guard import remote,merge_delta
from cache_operation_tags import tag_chat_calls

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/cache-layout-repair-merge'
BASELINE='0170057'
NEW={'tests/test_cache_layout_repair.py','deploy/cache_operation_tags.py'}
TAGS={'whalegirl_linux/moments.py':'moments','whalegirl_linux/stickers.py':'sticker',
      'whalegirl_linux/sticker_library.py':'sticker'}
DELTA={'whalegirl_linux/'+n for n in ('time_context.py','upstream/ai_engine.py','memory.py','runtime.py')}
FILES=NEW|set(TAGS)|DELTA


def main():
    live=json.loads(remote("import pathlib,base64,json\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(sorted(FILES))+
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))"))
    before={};after={};labels={};anchors={}
    for name in sorted(FILES):
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        if name in NEW:
            if raw is not None:raise RuntimeError('New cache source already exists: '+name)
            value=(ROOT/name).read_text(encoding='utf-8')
        else:
            if raw is None:raise RuntimeError('Missing live source: '+name)
            value=raw.decode().replace('\r\n','\n')
            saved=OUT/'server-before'/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(raw)
            if name in DELTA:
                previous=subprocess.check_output(['git','show',BASELINE+':'+name],cwd=ROOT).decode()
                value,anchors[name]=merge_delta(previous,(ROOT/name).read_text(encoding='utf-8'),value,name)
            if name in TAGS:value,labels[name]=tag_chat_calls(value,TAGS[name])
            if name=='whalegirl_linux/runtime.py':
                value,labels[name]=tag_chat_calls(value,'memory',function='_digest_loop')
                # One selected gallery feeds both the fixed catalog and matching
                # context. Retain live Tieba prefill and all other live deltas.
                old="        _, image_tool_context = self.images.tools.context(first, batch, include_prompt=False)\n        base_image_catalog = self.images.tools.base_candidates(first)\n"
                new="        base_image_catalog = self.images.tools.base_candidates(first)\n        _, image_tool_context = self.images.tools.context(\n            first, batch, include_prompt=False, base_candidates=base_image_catalog)\n"
                if value.count(old)!=1:raise RuntimeError('Changed live gallery layout')
                value=value.replace(old,new,1)
        compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(value,encoding='utf-8',newline='\n')
        after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':before,'after':after,
        'usage_labels_restored':labels,'unique_line_anchors':anchors},indent=2))
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in sorted(FILES):archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'files':len(FILES),'root':live['root'],'usage_labels_restored':labels,'anchors':anchors}))


if __name__=='__main__':main()
