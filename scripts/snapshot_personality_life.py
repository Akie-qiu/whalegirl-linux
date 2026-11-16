"""Create source-only local review/rollback artifacts, without runtime data."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import zipfile


ROOT=Path(__file__).resolve().parents[1]
SOURCE_DIRS={'whalegirl_linux','styles','tests','scripts','deploy','docs'}
SUFFIXES={'.py','.js','.mjs','.cjs','.html','.css','.md','.txt','.yaml','.yml',
    '.toml','.ps1','.sh','.json','.lock','.service','.timer','.path'}
SOURCE_NAMES={'.gitignore','.gitattributes','.gitmodules','LICENSE','NOTICE'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    out=args.out or ROOT/'artifacts'/'personality-life-20261106'/('source-snapshot-'+str(time.time_ns()))
    out.mkdir(parents=True,exist_ok=False)
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    names=subprocess.check_output(['git','ls-files','-z','--cached','--others','--exclude-standard'],cwd=ROOT).decode('utf-8').split('\0')
    manifest={'kind':'source-only-local-snapshot','git_head':revision,'created':time.time(),
        'production_data':False,'credentials':False,'files':{}}
    with zipfile.ZipFile(out/'candidate-source.zip','x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(set(names)):
            if not name:continue
            path=ROOT/name;relative=Path(name)
            if (path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT.resolve())
                    or (relative.parts[0] not in SOURCE_DIRS and len(relative.parts)>1)
                    or not (path.suffix in SUFFIXES or path.name in SOURCE_NAMES
                        or path.name.startswith(('Dockerfile.','NOTICE-')))
                    or any(part in ('__pycache__','data','artifacts','test-runs','.venv','.git') for part in relative.parts)
                    or '.before-' in path.name):continue
            content=path.read_bytes()
            archive.writestr(relative.as_posix(),content)
            manifest['files'][relative.as_posix()]={'sha256':hashlib.sha256(content).hexdigest(),'bytes':len(content)}
    subprocess.run(['git','archive','--format=zip','--output='+str((out/'git-head-source.zip').resolve()),revision],cwd=ROOT,check=True)
    baseline=ROOT/'artifacts'/'personality-life-20261106'/'server-baseline-manifest.json'
    if baseline.exists():manifest['server_source_baseline']=json.loads(baseline.read_text('utf-8'))
    for name in ('candidate-source.zip','git-head-source.zip'):
        path=out/name;manifest[name]={'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({'path':str(out.resolve()),'source_files':len(manifest['files']),
        'archive_bytes':sum(manifest[name]['bytes'] for name in ('candidate-source.zip','git-head-source.zip')),
        'production_data':False,'credentials':False},ensure_ascii=True))


if __name__=='__main__':main()
