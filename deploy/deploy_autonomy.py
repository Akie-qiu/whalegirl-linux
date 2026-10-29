"""Core-only upgrade, then activate the administrator's reversible mode switch."""
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

import deploy_moments_recovery as rollout

rollout.DEPLOYMENT = 'autonomy-upgrade'
rollout.FILES = {'whalegirl_linux/'+name for name in (
    'app.py','conversation.py','habits.js','proactive.py','runtime.py','settings.py','web.html','autonomy.py')}
rollout.FILES.add('tests/test_autonomy.py')
rollout.FILES.add('tests/test_proactive.py')
rollout.TEST_MODULES = ('test_autonomy',)
rollout.CONFIG_DEFAULTS = {'autonomy_enabled':False}
rollout.REQUIRE_ONLINE = False  # Preserve a pre-existing offline state and hold.


def main():
    rollout.main()
    report_path = rollout.ASSETS/'autonomy-upgrade-deployment.json'
    report = json.loads(report_path.read_text())
    backup = Path(report['backup'])
    current = Path(report['release'])
    sys.path.insert(0,str(current))
    from whalegirl_linux.settings import Settings
    config = rollout.api('config')
    old = {key:value for key,value in config.items() if key in Settings.model_fields}
    values = {**old,'autonomy_enabled':True}
    saved_settings = backup/'settings-before-autonomy.json'
    saved_settings.write_bytes((rollout.DATA/'settings.json').read_bytes())
    saved_settings.chmod(0o600)
    key_names = ('api-key.enc','image-api-key.enc','admin.token')
    credentials = {name:rollout.digest(rollout.DATA/name) for name in key_names}
    request = urllib.request.Request('http://127.0.0.1:8092/api/config',method='POST',
        data=json.dumps({'settings':values}).encode(),headers={
            'Authorization':'Bearer '+(rollout.DATA/'admin.token').read_text().strip(),
            'Content-Type':'application/json'})
    with rollout.OPENER.open(request,timeout=15) as response:
        response.read()
    updated = rollout.api('config')
    preserved = all(updated.get(key)==value for key,value in old.items() if key!='autonomy_enabled')
    keys_preserved = all(rollout.digest(rollout.DATA/name)==value for name,value in credentials.items())
    state = rollout.api('state')
    agency = rollout.api('autonomy')
    if not preserved or not keys_preserved or not agency['enabled']:
        raise RuntimeError('Autonomy activation did not preserve existing settings and credentials')
    report.update({'new_logic_enabled':True,'existing_settings_preserved':preserved,
        'credentials_preserved_after_activation':keys_preserved,'test_messages_sent':0,
        'wechat_status_after_activation':state.get('gateway',{}).get('status'),
        'existing_pause_preserved':bool(state.get('paused_reason')) if report.get('wechat_status')!='logged_in' else True,
        'switch':'聊天习惯 → 新版自主聊天','snapshot_hash':hashlib.sha256(saved_settings.read_bytes()).hexdigest()})
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({key:value for key,value in report.items() if key not in ('hashes','snapshot_hash')},ensure_ascii=False))


if __name__ == '__main__':
    main()
