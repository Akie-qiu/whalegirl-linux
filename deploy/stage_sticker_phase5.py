"""Stage targeted native sticker snapshots without unrelated local changes."""
import base64
import hashlib
import json
from pathlib import Path
import tarfile
from stage_tool_reply_guard import remote,merge_delta

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'artifacts/reply-sticker-phase5'
NEW={'tests/test_sticker_subtree.py','tests/test_sticker_subtree_delivery.py'}
NATIVE={'deploy/a11y_safe.py':'/opt/tools/a11y-dump','deploy/chat_select_linux.py':'/opt/tools/chat-select.py'}
FILES=NEW|set(NATIVE)


def main():
    live=json.loads(remote("import pathlib,base64,json,subprocess\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(sorted(FILES))+
        "\nnative="+repr(NATIVE)+"\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names},'mounted':{n:base64.b64encode(subprocess.check_output(['docker','exec','whalegirl-wechat-candidate-4118','cat',p],timeout=8)).decode() for n,p in native.items()}}))"))
    before={};after={};reviewed={};mounted={}
    for name in sorted(FILES):
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        if name in NEW:
            if raw is not None:raise RuntimeError('New file already present: '+name)
            value=(ROOT/name).read_text(encoding='utf-8')
        else:
            if name in NATIVE:
                active=base64.b64decode(live['mounted'][name])
                if active!=raw:raise RuntimeError('Native file differs from release: '+name)
                mounted[name]=hashlib.sha256(active).hexdigest()
            saved=OUT/'server-before'/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(raw)
            value,reviewed[name]=merge_delta((OUT/'local-before'/name).read_text(encoding='utf-8'),
                (ROOT/name).read_text(encoding='utf-8'),raw.decode('utf-8'),name)
        compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(value,encoding='utf-8',newline='\n');after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':before,'after':after,
        'mounted_before':mounted,'unique_line_anchors':reviewed},indent=2),encoding='utf-8')
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in sorted(FILES):archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'root':live['root'],'files':len(FILES),'unique_line_anchors':reviewed}))


if __name__=='__main__':main()
