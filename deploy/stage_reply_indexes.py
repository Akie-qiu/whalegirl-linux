"""Indexes for the measured send-limit query bottleneck, without data changes."""
import base64
import hashlib
import json
from pathlib import Path
import tarfile
from stage_tool_reply_guard import remote,merge_delta

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'artifacts/reply-indexes';NAME='whalegirl_linux/store.py'


def main():
    live=json.loads(remote("import pathlib,base64,json\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nprint(json.dumps({'root':str(r),'data':base64.b64encode((r/"+repr(NAME)+").read_bytes()).decode()}))"))
    raw=base64.b64decode(live['data']);baseline=(OUT/'local-before'/NAME).read_text(encoding='utf-8')
    value,anchors=merge_delta(baseline,(ROOT/NAME).read_text(encoding='utf-8'),raw.decode(),NAME)
    compile(value,NAME,'exec')
    before=OUT/'server-before'/NAME;before.parent.mkdir(parents=True,exist_ok=True);before.write_bytes(raw)
    target=OUT/'payload'/NAME;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(value,encoding='utf-8',newline='\n')
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':{NAME:hashlib.sha256(raw).hexdigest()},
        'after':{NAME:hashlib.sha256(target.read_bytes()).hexdigest()}},indent=2))
    with tarfile.open(OUT/'release.tar','w') as archive:archive.add(target,arcname=NAME)
    print(json.dumps({'root':live['root'],'files':1,'unique_line_anchors':anchors}))


if __name__=='__main__':main()
