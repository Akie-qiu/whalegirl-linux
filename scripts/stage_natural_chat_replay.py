"""Build a reviewed source-only candidate; never touches a running deployment.

Production is read-only. The candidate and all conflict/output files are kept.
No credentials, settings, databases, native actions, cleanup or Git push.
"""
import base64
import ast
import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/natural-chat-20261102'
SOURCE_FILES = [
    'runtime.py', 'context.py', 'conversation.py', 'affection.py', 'experience.py',
    'autonomy.py', 'learning.py', 'memory.py', 'memory_hub.py', 'proactive.py',
    'app.py', 'settings.py', 'console.js', 'habits.js', 'improvements.js', 'proactive.js', 'memory-hub.js', 'web.html',
    'natural_chat.py', 'chat_style.py', 'persona_revision.py', 'social_context.py',
]


def ssh(script):
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                             'root@120.46.186.50', '/opt/whalegirl-linux/venv/bin/python', '-'],
                            input=script, text=True, encoding='utf-8', capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError('Read-only source fetch failed: ' + result.stderr[-1000:])
    return json.loads(result.stdout)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'production-full-source.json').exists():
        envelope=json.loads((OUT / 'production-full-source.json').read_text('utf-8'))
    else:
        envelope = ssh('''import base64,hashlib,io,json,tarfile
from pathlib import Path
root=Path('/opt/whalegirl-linux/current').resolve()
items=[]
for name in ('whalegirl_linux','styles','tests'):
    for p in (root/name).rglob('*'):
        if p.is_file() and p.suffix in ('.py','.js','.html','.css','.yaml','.yml') and '__pycache__' not in p.parts:
            items.append(p)
buf=io.BytesIO()
with tarfile.open(fileobj=buf,mode='w:gz') as tar:
    for p in sorted(items):tar.add(p,arcname=str(p.relative_to(root)),recursive=False)
print(json.dumps({'release':str(root),'source':base64.b64encode(buf.getvalue()).decode(),
    'sha256':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in items}}))
''')
        (OUT / 'production-full-source.tar.gz').write_bytes(base64.b64decode(envelope.pop('source')))
        (OUT / 'production-full-source.json').write_text(json.dumps(envelope, indent=2), 'utf-8')
    baseline = OUT / 'baseline-source'
    candidate = OUT / 'candidate-source'
    for target in (baseline, candidate):
        with tarfile.open(OUT / 'production-full-source.tar.gz', 'r:gz') as tar:
            for member in tar.getmembers():
                path = target / member.name
                if not member.isfile() or not path.resolve().is_relative_to(target.resolve()):
                    raise ValueError('Unexpected source archive member')
                path.parent.mkdir(parents=True, exist_ok=True)
                content=tar.extractfile(member).read()
                if target==baseline and path.exists() and path.read_bytes()!=content:
                    raise RuntimeError('Saved production snapshot changed: '+str(path))
                path.write_bytes(content)
    reviews=[]
    conflicts=[]
    for name in SOURCE_FILES:
        rel=Path('whalegirl_linux')/name
        local=(ROOT/rel).read_text('utf-8')
        latest=baseline/rel
        common=OUT/'local-before'/rel
        if name in ('runtime.py','context.py'):
            common=ROOT/'artifacts/social-judgment-20261102/local-before'/rel
        if name in ('console.js','improvements.js','proactive.js','web.html'):
            # Latest production supplied these unmodified local UI sources.
            # This task replaces the old voice/mode controls, leaving data.
            common=latest
        if not latest.exists():
            result=local;method='new_module'
        elif not common.exists():
            raise RuntimeError('Missing reviewed merge base: '+str(common))
        elif latest.read_text('utf-8')==common.read_text('utf-8'):
            result=local;method='local_change_on_identical_production'
        elif common.read_text('utf-8')==local:
            result=latest.read_text('utf-8');method='production_only_change'
        else:
            inputs=OUT/'merge-inputs'/name
            inputs.mkdir(parents=True,exist_ok=True)
            normalized=[]
            for label,p in [('production',latest),('common',common),('local',ROOT/rel)]:
                q=inputs/label;q.write_text(p.read_text('utf-8'),'utf-8',newline='\n');normalized.append(str(q))
            merge=subprocess.run(['git','merge-file','-p',*normalized],
                                  text=True,encoding='utf-8',capture_output=True)
            result=merge.stdout
            method='three_way_merge'
            if merge.returncode:
                conflict=OUT/(name.replace('/','_')+'.merge-conflict')
                conflict.write_text(result,'utf-8')
                blocks=list(re.finditer(r'^<<<<<<<[^\n]*\n(.*?)^=======\n(.*?)^>>>>>>>[^\n]*\n',result,re.M|re.S))
                anchors={
                    'runtime.py':[("            static_system+='",'            static_system+=reply_protocol()')],
                    'experience.py':[('【体验与相处】','【体验资料】')],
                    'conversation.py':[('    experienced = bool(',"    out=''"),
                                       ('    if not settings.stickers_enabled:', '    from .natural_chat import sticker_policy')],
                    'context.py':[('    experienced = settings.autonomy_enabled','    context_limit=settings.context_messages'),
                                  ("    new_heading = '","    new_heading = '")],
                    'affection.py':[("        group=first['chat']", "        if first['chat'].endswith('@chatroom'):"),
                                    ('            owner=self.is_owner(', "            row={'sender_id':subject")],
                }.get(name,[])
                known=len(blocks)==len(anchors) and all(b[1].startswith(a[0]) and b[2].startswith(a[1]) for b,a in zip(blocks,anchors))
                if not known:
                    conflicts.append(name);continue
                # Reviewed above: new compact protocol/header supersedes the
                # old schema and repeated behavioral paragraphs respectively.
                for block in reversed(blocks):
                    result=result[:block.start()]+block[2]+result[block.end():]
                # Whole voice functions were intentionally replaced. Keep
                # unrelated production functions while making these exact.
                voice_names={
                    'conversation.py':{'habit_static_prompt','habit_dynamic_prompt','warmth_prompt','sticker_static_prompt',
                                       'persona_live_dynamic_prompt','update_mood_after_turn','sticker_usage_prompt'},
                    'affection.py':{'venue_prompt','static_prompt','dynamic_prompt'},
                }.get(name,set())
                if voice_names:
                    originals={n.name:ast.get_source_segment(local,n) for n in ast.walk(ast.parse(local))
                               if isinstance(n,ast.FunctionDef) and n.name in voice_names}
                    lines=result.splitlines(keepends=True)
                    replacements=[n for n in ast.walk(ast.parse(result)) if isinstance(n,ast.FunctionDef) and n.name in voice_names]
                    for n in sorted(replacements,key=lambda n:n.lineno,reverse=True):
                        source=originals[n.name].splitlines()
                        lines[n.lineno-1:n.end_lineno]=[' '*n.col_offset+source[0]+'\n',*[s+'\n' for s in source[1:]]]
                    result=''.join(lines)
                method='reviewed_protocol_or_header_conflict'
        if name=='runtime.py' and '        tieba_context, tieba_context_mode = select_topic_history(' in result:
            start=result.index('        tieba_context, tieba_context_mode = select_topic_history(')
            end=result.index("        timing['tieba_recall']",start)
            if 'self.tieba.prompt(' not in result[start:end]:
                raise RuntimeError('Unexpected Tieba context difference')
            # Explicit reviewed change: retrieved content is now tool-driven,
            # with the same available tools and source checks preserved.
            result=result[:start]+result[end:]
        target=candidate/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(result, 'utf-8', newline='\n')
        reviews.append({'file':str(rel),'method':method,'sha256':hashlib.sha256(result.encode()).hexdigest()})
    # Current local tests exercise the candidate in an isolated Store.
    for source in (ROOT/'tests').glob('*.py'):
        target=candidate/'tests'/source.name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(source.read_bytes())
    # Existing local accounting UI/API are newer than the online release.
    # Carry that reviewed additive implementation, not just its newer tests.
    (candidate/'whalegirl_linux/token_usage.py').write_bytes((ROOT/'whalegirl_linux/token_usage.py').read_bytes())
    for source in (ROOT/'deploy').glob('*.py'):
        target=candidate/'deploy'/source.name
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(source.read_bytes())
    report={'release':envelope['release'],'files':reviews,'conflicts':conflicts}
    (OUT/'merge-review.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({'files':len(reviews),'conflicts':conflicts,'candidate':str(candidate)},ensure_ascii=True))
    if conflicts:raise SystemExit(1)


if __name__=='__main__':main()
