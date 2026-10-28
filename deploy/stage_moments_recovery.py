"""Stage daily schedule recovery and consistent Moments character references."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

from stage_tool_reply_guard import merge_delta

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/moments-recovery'
EXISTING=['whalegirl_linux/'+name for name in ('moments.py','images.py','image_references.py','image_retention.py')]+['tests/test_moments_references.py']
NEW=['tests/test_moments_recovery.py']
FILES=EXISTING+NEW


def remote(code):
    return subprocess.run(['ssh.exe','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','ConnectionAttempts=2','root@120.46.186.50','python3','-'],
        input=code,text=True,capture_output=True,encoding='utf-8',check=True,timeout=60).stdout


def main():
    live=json.loads(remote("import pathlib,base64,json\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(FILES)+
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))"))
    before={};after={};anchors={}
    for name in FILES:
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        if name in NEW:
            if raw is not None:raise RuntimeError('New task file exists remotely')
            value=(ROOT/name).read_text(encoding='utf-8')
        else:
            saved=OUT/'server-before'/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(raw)
            previous=(OUT/'local-before'/name).read_text(encoding='utf-8')
            updated=(ROOT/name).read_text(encoding='utf-8')
            production=raw.decode('utf-8')
            if name=='whalegirl_linux/moments.py':
                # The live call predates the local unfinished enqueue wrapper.
                # Explicitly attach the backend-selected variant in this task.
                old="                            self.runtime.images.enqueue(kind='generate',prompt=image_prompt,\n                                destination='moments',moment_id=identity,source_key='moments:'+identity)\n"
                if old in production:
                    new="                            self.runtime.images.enqueue(kind='generate',prompt=image_prompt,destination='moments',\n                                moment_id=identity,source_key='moments:'+identity,character=True,character_variant=s.image_character_variant)\n"
                    production=production.replace(old,new,1)
                    previous=previous.replace("variant = value.get('character_variant') if value.get('character_variant') in ('chibi', 'girl', 'mature') else ''",'variant = s.image_character_variant')
            value,anchors[name]=merge_delta(previous,updated,production,name)
        compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(value,encoding='utf-8',newline='\n');after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':before,'after':after,'unique_line_anchors':anchors},indent=2),encoding='utf-8')
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in FILES:archive.add(OUT/'payload'/name,arcname=name)
    print(json.dumps({'staged':len(FILES),'root':live['root'],'unique_line_anchors':anchors}))


if __name__=='__main__':main()
