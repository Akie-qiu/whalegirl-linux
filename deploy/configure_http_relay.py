"""Read the admin-supplied relay key from stdin; save via the existing API."""
import json
from pathlib import Path
import sys
import urllib.request

DATA=Path('/var/lib/whalegirl-linux/core')
OUT=Path('/opt/whalegirl-linux-assets/http-relay-zero-20261030')
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))
TOKEN=(DATA/'admin.token').read_text().strip()


def api(route,body=None,timeout=15):
    request=urllib.request.Request('http://127.0.0.1:8092'+route,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json'})
    with OPENER.open(request,timeout=timeout) as response:return json.load(response)


def main():
    payload=json.load(sys.stdin);key=payload['api_key'].strip()
    if not key:raise RuntimeError('Missing relay key')
    before=api('/api/config')
    values={'api_url':'http://154.9.235.108:8080/v1','api_style':'openai',
            'model':'deepseek-v4.1-flash','chat_cooldown':0,'min_send_interval':0}
    after=api('/api/config',{'settings':values,'api_key':key})
    unexpected=[name for name in before if before[name]!=after[name] and name not in values and name!='api_key_configured']
    if unexpected:raise RuntimeError('Unexpected changed settings: '+str(unexpected))
    test=api('/api/model/test',{},timeout=35)
    state=api('/api/state');health=api('/health')
    report={'settings':{name:after[name] for name in values},'key_configured':after['api_key_configured'],
            'other_settings_preserved':not unexpected,'enabled_preserved':before['enabled']==after['enabled'],
            'model_test':{k:test.get(k) for k in ('ok','message','elapsed_ms','api_style','stream_enabled')},
            'wechat_status':state['gateway'].get('status'),'enabled':state['enabled'],
            'paused_reason':state.get('paused_reason'),'health':health}
    (OUT/'connection-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False))
    if not test.get('ok'):raise RuntimeError('Relay model connection test failed')


if __name__=='__main__':main()
