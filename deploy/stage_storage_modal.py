"""Merge only native recovery and completed-navigation receipt changes."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
from stage_tool_reply_guard import remote,merge_delta

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/storage-modal'
NEW={'tests/test_storage_modal_recovery.py','tests/test_ui_job_completion.py'}
FILES={'whalegirl_linux/gateway.py','deploy/native_recovery.py'}|NEW


def main():
    live=json.loads(remote("import base64,json\nfrom pathlib import Path\nr=Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(sorted(FILES))+
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))"))
    before={};after={};anchors={}
    for name in sorted(FILES):
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        if name in NEW:
            if raw is not None:raise RuntimeError('New test already exists: '+name)
            value=(ROOT/name).read_text(encoding='utf-8')
        else:
            if raw is None:raise RuntimeError('Live source missing: '+name)
            previous=subprocess.check_output(['git','show','d8567a3:'+name],cwd=ROOT).decode()
            value,anchors[name]=merge_delta(previous,(ROOT/name).read_text(encoding='utf-8'),raw.decode(),name)
        compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(value,encoding='utf-8',newline='\n')
        after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':before,'after':after,'unique_line_anchors':anchors},indent=2))
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in sorted(FILES):archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'files':len(FILES),'root':live['root'],'unique_line_anchors':anchors}))


if __name__=='__main__':main()
