"""Review and remove the live-only legacy prefill/duplicated catalog layout."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
from stage_tool_reply_guard import remote,merge_delta

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/proactive-cache-merge'
FILES={'whalegirl_linux/proactive.py','tests/test_proactive_cache.py'}
BASELINE='77ccf35'


def main():
    live=json.loads(remote("import pathlib,base64,json\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(sorted(FILES))+
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))"))
    before={};after={}
    for name in sorted(FILES):
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        value=(ROOT/name).read_text(encoding='utf-8')
        if name.endswith('proactive.py'):
            if raw is None:raise RuntimeError('Missing proactive source')
            saved=OUT/'server-before'/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(raw)
            production=raw.decode().replace('\r\n','\n')
            previous=subprocess.check_output(['git','show',BASELINE+':'+name],cwd=ROOT).decode().replace('\r\n','\n')
            replacements=[
                ("            unused, image_context = r.images.tools.context(first, [first], query=query)\n",
                 "            base_image_catalog = r.images.tools.base_candidates(first)\n            unused, image_context = r.images.tools.context(\n                first, [first], query=query, base_candidates=base_image_catalog)\n            base_image_ids = {item['id'] for item in base_image_catalog}\n"),
                ("            tieba_prompt = r.tieba.prompt(query, tieba_evidence, chat=chat)\n",''),
                ("                + r.images.tools.static_prompt(first) + sticker_static_prompt(s)\n",
                 "                + r.images.tools.static_prompt(first, include_catalog=False)\n                + r.images.tools.catalog_prompt(base_image_catalog) + sticker_static_prompt(s)\n"),
                ("            dynamic = (memory_dynamic + tieba_prompt + r.images.tools.dynamic_prompt(image_context)\n",
                 "            dynamic = (memory_dynamic + r.images.tools.dynamic_prompt(image_context, base_image_ids)\n")]
            for old,new in replacements:
                if production.count(old)!=1:raise RuntimeError('Reviewed legacy cache layout changed')
                production=production.replace(old,new,1)
            if production!=previous:raise RuntimeError('Additional live proactive drift needs review')
            value,_=merge_delta(previous,value,production,name)
        elif raw is not None:raise RuntimeError('New cache test already exists')
        compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(value,encoding='utf-8',newline='\n')
        after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':before,'after':after},indent=2))
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in sorted(FILES):archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'files':len(FILES),'reviewed_legacy_cache_changes':4,'root':live['root']}))


if __name__=='__main__':main()
