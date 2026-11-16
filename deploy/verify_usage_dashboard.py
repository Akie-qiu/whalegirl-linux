"""Post-deploy read-only verification: never constructs a production Store."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace
from urllib.parse import urlencode
import urllib.request

DATA=Path('/var/lib/whalegirl-linux/core')


def main():
    folder=Path(sys.argv[1]);manifest=json.loads((folder/'manifest.json').read_text())
    current=Path('/opt/whalegirl-linux/current').resolve()
    assert current==Path(manifest['release'])
    sys.path.insert(0,str(current))
    token=(DATA/'admin.token').read_text().strip()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(path):
        request=urllib.request.Request('http://127.0.0.1:8092'+path,headers={'Authorization':'Bearer '+token})
        with opener.open(request,timeout=25) as response:return json.load(response)
    health=api('/health');state=api('/api/state');settings=api('/api/config')
    assert health['status']=='ok' and health['failed_background_tasks']==0
    assert state.get('gateway',{}).get('status')=='logged_in' and not state.get('paused_reason')
    db=sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    try:
        from whalegirl_linux.usage_dashboard import UsageDashboard
        from whalegirl_linux.usage_tools import UsageStatsTools
        runtime=SimpleNamespace(store=SimpleNamespace(db=db,lock=threading.RLock()),
            config=SimpleNamespace(settings=SimpleNamespace(**settings)),chats_cache=[],chat_labels={})
        runtime.statistics=UsageDashboard(runtime)
        tools=UsageStatsTools(runtime)
        chat=next((c for c in settings['allowed_chat_ids'] if c.endswith('@chatroom')),settings['allowed_chat_ids'][0])
        first={'account':settings['account_id'],'chat':chat,'sender_id':settings.get('owner_ids',[''])[0]}
        scoped=tools.execute(first,{'view':'both','period':'today'})
        denied=tools.execute(first,{'scope':'account'})
        assert scoped['ok'] and scoped['scope']=='current_chat'
        if chat.endswith('@chatroom'):assert not denied['ok']
        assert len(db.execute('PRAGMA table_info(model_token_usage)').fetchall())==15
        native=tools.schemas(SimpleNamespace(api_style=settings.get('api_style'),
            stream_enabled=settings.get('model_stream_enabled'),chat_with_tools=True))
        dashboard=api('/api/usage-dashboard?period=today&view=both')
        filtered=api('/api/usage-dashboard?'+urlencode({'period':'today','view':'tokens','chat':chat}))
        assert filtered['tokens']['summary']['requests']==scoped['tokens']['requests']
        hashes={name:hashlib.sha256((current/name).read_bytes()).hexdigest()==value for name,value in manifest['after'].items()}
        assert all(hashes.values())
        result={'health':health,'wechat':'logged_in','paused':False,'all_payload_hashes_match':True,
            'original_usage_columns':15,'read_only_tool_current_scope':True,
            'group_account_scope_denied':not denied['ok'] if chat.endswith('@chatroom') else None,
            'current_chat_api_matches_tool':True,'native_tool_offered':bool(native),
            'json_statistics_bridge_present':'stats_request' in tools.prompt(),
            'real_model_calls':0,'validation_messages_sent':0,
            'requests_today':dashboard['tokens']['summary']['requests'],
            'sent_today':dashboard['speech']['summary']['sent'],
            'query_ms':dashboard['query_ms'],
            'attributed_requests':db.execute('SELECT count(*) FROM model_usage_context').fetchone()[0]}
        (folder/'read-only-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        (folder/'read-only-verification.json').chmod(0o600)
        print(json.dumps(result,ensure_ascii=False))
    finally:db.close()


if __name__=='__main__':main()
