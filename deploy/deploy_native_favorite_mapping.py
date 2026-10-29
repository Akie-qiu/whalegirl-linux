"""Hot-publish a verified favorite locator while sending is already paused."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

CONTAINER='whalegirl-wechat-candidate-4118'
SOURCE=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools/chat_select_linux.py')
MOUNTED='/opt/tools/chat-select.py'
DATA=Path('/var/lib/whalegirl-linux/core')
ASSETS=Path('/opt/whalegirl-linux-assets')


def sha(value):return hashlib.sha256(value).hexdigest()


def command(*args):return subprocess.check_output(args,timeout=12)


def api(route,body=None):
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        headers={'Authorization':'Bearer '+(DATA/'admin.token').read_text().strip(),
                 'Content-Type':'application/json'},
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(request,timeout=35) as response:return json.load(response)


def replace_source(content):
    stat=SOURCE.stat();pending=SOURCE.with_name(SOURCE.name+'.mapping-'+str(time.time_ns()))
    pending.write_bytes(content);os.chown(pending,stat.st_uid,stat.st_gid);pending.chmod(stat.st_mode&0o777)
    os.replace(pending,SOURCE)


def write_mounted(content):
    subprocess.run(['docker','exec','-i',CONTAINER,'python3','-c',
        'import pathlib,sys;pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())',MOUNTED],
        input=content,capture_output=True,check=True,timeout=12)


def main():
    lock=(ASSETS/'native-favorite-mapping-deploy.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    candidate=Path(sys.argv[1]).read_bytes();manifest=json.loads(Path(sys.argv[2]).read_text())
    compile(candidate,str(SOURCE),'exec')
    before=SOURCE.read_bytes();mounted=command('docker','exec',CONTAINER,'cat',MOUNTED)
    if (sha(before)!=manifest['source_before'] or sha(mounted)!=manifest['mounted_before']
            or sha(candidate)!=manifest['after']):raise RuntimeError('Native source changed after review')
    probe=json.loads(Path(sys.argv[1]).with_name('native-favorite-probe.json').read_text())
    if probe.get('located') is not True or probe.get('send_blocked') is not True:
        raise RuntimeError('No-send probe has not located the failed favorite')
    state=api('state')
    if state.get('processing') or not state.get('paused_reason'):
        raise RuntimeError('Expected an idle core already held for the native send failure')
    if state.get('gateway',{}).get('status')!='logged_in':raise RuntimeError('WeChat is offline')
    jobs=SOURCE.parent.parent/'whalegirl-jobs'
    for path in jobs.glob('*.json'):
        try:job=json.loads(path.read_text())
        except (OSError,ValueError):continue
        if time.time()-job.get('created',0)<35 and job.get('state','') in ('','sending','collecting'):
            raise RuntimeError('Native UI job is still active')
    protected={name:sha((DATA/name).read_bytes()) for name in ('settings.json','api-key.enc','image-api-key.enc','admin.token')}
    identities={
        'wechat_pid':command('docker','exec',CONTAINER,'pgrep','-x','wechat'),
        'reader_pid':command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value'),
        'container':command('docker','inspect','--format','{{.Id}} {{.RestartCount}}',CONTAINER)}
    backup=ASSETS/('native-favorite-mapping-before-'+str(time.time_ns()));backup.mkdir(mode=0o700)
    (backup/'saved-source.py').write_bytes(before);(backup/'mounted-selector.py').write_bytes(mounted)
    try:
        replace_source(candidate);write_mounted(candidate)
        if sha(SOURCE.read_bytes())!=manifest['after'] or sha(command('docker','exec',CONTAINER,'cat',MOUNTED))!=manifest['after']:
            raise RuntimeError('Native publish verification failed')
    except BaseException:
        replace_source(before);write_mounted(mounted)
        raise
    recovery=api('recovery/run',{'resume':True})
    after=api('state')
    preserved={
        'wechat_pid':command('docker','exec',CONTAINER,'pgrep','-x','wechat')==identities['wechat_pid'],
        'reader_pid':command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')==identities['reader_pid'],
        'container':command('docker','inspect','--format','{{.Id}} {{.RestartCount}}',CONTAINER)==identities['container'],
        'configuration_and_keys':all(sha((DATA/name).read_bytes())==digest for name,digest in protected.items())}
    report={'backup':str(backup),'sha256':manifest['after'],'no_send_probe':probe,'preserved':preserved,
            'wechat_status':after.get('gateway',{}).get('status'),'paused_reason':after.get('paused_reason'),
            'recovery':recovery,'real_test_messages_sent':0}
    (ASSETS/'native-favorite-mapping-deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False))
    if not all(preserved.values()) or after.get('paused_reason'):
        raise RuntimeError('Native repair requires review; see the retained deployment report')


if __name__=='__main__':main()
