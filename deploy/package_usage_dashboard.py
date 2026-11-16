"""Package only the tested feature files over a captured live baseline."""
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/usage-dashboard-deploy-20261111'
FILES=['whalegirl_linux/'+n for n in ('app.py','runtime.py','proactive.py','proactive_tools.py','token_usage.py',
    'studio.js','studio.css','web.html','usage_dashboard.py','usage_tools.py','usage_context.py','usage_pricing.py',
    'usage-dashboard.js','usage-dashboard.css')]+['tests/test_usage_dashboard.py','tests/test_studio_console.py']


def main():
    original=json.loads((ROOT/'artifacts/usage-dashboard-20261110/manifest.json').read_text(encoding='utf-8-sig'))
    guard=json.loads((OUT/'source-guard.json').read_text(encoding='utf-8-sig'))
    expected={('whalegirl_linux/'+r['name']):r['after_sha256'] for r in original['existing']}
    expected.update({r['path']:r['sha256'] for r in original['added']})
    for name in FILES:
        digest=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
        if name in expected and digest!=expected[name]:raise RuntimeError('Candidate changed since tests: '+name)
    for row in original['existing']:
        if row['name'] in ('app.py','runtime.py','proactive.py','proactive_tools.py','token_usage.py'):
            if guard['source_guard'].get('whalegirl_linux/'+row['name'])!=row['before_sha256']:
                raise RuntimeError('Live backend needs a manual merge: '+row['name'])
    stamp=datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')
    name='usage-dashboard-'+stamp
    manifest={'root':guard['root'],'release':'/opt/whalegirl-linux/releases/'+name,
        'backup':'/opt/whalegirl-linux-assets/'+name,'source_guard':guard['source_guard'],
        'before':{name:guard['source_guard'].get(name) for name in FILES},
        'after':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in FILES},
        'validation_cpu_quota':'50%','validation_memory_max':'256M','validation_swap_max':'128M'}
    with tarfile.open(OUT/'payload.tar.gz','w:gz') as archive:
        for name in FILES:archive.add(ROOT/name,arcname=name,recursive=False)
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'files':len(FILES),'source_guard_files':len(manifest['source_guard']),
                      'archive_bytes':(OUT/'payload.tar.gz').stat().st_size,'release':manifest['release'],
                      'backup':manifest['backup']}))


if __name__=='__main__':main()
