"""Deploy only the reviewed model manager diff over the current live release."""
import hashlib
import json
from pathlib import Path
import sys
import tarfile

import deploy_moments_recovery as rollout

TAG='model-console-20261116'

def hashes(root):
    result={}
    for folder in ('whalegirl_linux','deploy','styles'):
        for p in (root/folder).rglob('*'):
            rel=p.relative_to(root)
            if not p.is_file() or p.is_symlink() or {'__pycache__','test-runs','node_modules','.git','deployed-snapshot'}.intersection(rel.parts):continue
            if p.suffix not in ('.py','.js','.css','.html','.json','.yaml','.yml','.toml','.txt','.md','.png','.jpg','.webp','.woff','.woff2'):continue
            result[rel.as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    return result

def main():
    manifest=json.loads(Path(sys.argv[2]).read_text());previous=Path(manifest['root']);folder=Path(sys.argv[2]).parent
    rollout.DEPLOYMENT=TAG;rollout.FILES=set(manifest['after'])
    rollout.TEST_MODULES=('test_model_management','test_model_profiles')
    if hashes(previous)!=manifest['baseline']:raise RuntimeError('Production source changed; merge before deploying')
    protected={p.name:rollout.digest(p) for p in rollout.DATA.iterdir() if p.is_file() and
        (p.suffix in ('.enc','.key','.token') or p.name in ('settings.json','persona-cards.json','model-profiles.json','console-password.json'))}
    with tarfile.open(folder/'private-config-before.tar.gz','w:gz') as archive:
        for name in protected:archive.add(rollout.DATA/name,arcname=name,recursive=False)
    (folder/'private-config-before.tar.gz').chmod(0o600)
    original_api=rollout.api
    def api(route):
        result=original_api(route)
        if route=='config':
            # New presentation metadata differs; all connection values still
            # have to match exactly. Private file hashes independently guard it.
            for profile in result.get('model_profiles',{}).values():
                profile.pop('name',None);profile.pop('pricing',None)
        return result
    rollout.api=api
    original_switch=rollout.switch
    def switch(target,stamp):
        if target!=previous:
            if hashes(previous)!=manifest['baseline']:raise RuntimeError('Production source changed during staging')
            if any(rollout.digest(rollout.DATA/name)!=value for name,value in protected.items()):raise RuntimeError('Private configuration changed during staging')
            expected={**manifest['baseline'],**{n:h for n,h in manifest['after'].items() if not n.startswith('tests/')}}
            if hashes(target)!=expected:raise RuntimeError('Candidate source preservation failed')
            context={'release':str(target),'previous':str(previous),'private_backup':str(folder/'private-config-before.tar.gz')}
            context_path=folder/'rollback-context.json';context_path.write_text(json.dumps(context,indent=2));context_path.chmod(0o600)
        original_switch(target,stamp)
    rollout.switch=switch
    rollout.main()
    report_path=rollout.ASSETS/(TAG+'-deployment.json');report=json.loads(report_path.read_text())
    expected={**manifest['baseline'],**{n:h for n,h in manifest['after'].items() if not n.startswith('tests/')}}
    report.update(unrelated_source_preserved=hashes(Path(report['release']))==expected,
        all_private_configuration_preserved=all(rollout.digest(rollout.DATA/name)==h for name,h in protected.items()),
        private_backup=str(folder/'private-config-before.tar.gz'),
        rollback_command=f"/opt/whalegirl-linux/venv/bin/python {folder}/rollback_model_console.py {folder}/rollback-context.json")
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:report[k] for k in ('unrelated_source_preserved','all_private_configuration_preserved','private_backup','rollback_command')}))

if __name__=='__main__':main()
