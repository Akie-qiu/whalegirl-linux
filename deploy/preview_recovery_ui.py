"""Local console fixture; no external WeChat or model calls."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from whalegirl_linux.app import create_app
from whalegirl_linux.settings import Settings


class Gateway:
    login = {}
    ui_jobs_directory = None
    inflight_sends = set()
    ui_lock = asyncio.Lock()
    async def status(self, **kwargs):return {'status':'logged_in', 'loggedInUser':'preview'}
    async def safety_notice(self):return ''
    async def reset_ui(self, *args):return {'main_ready':True}
    async def native_request(self, *args, **kwargs):return {'stickers':[]}
    async def chats(self):return [{'id':'preview@chatroom', 'name':'演示会话', 'isGroup':True}]
    async def close(self):pass


if __name__ == '__main__':
    os.environ['WHALEGIRL_PUBLIC_ORIGINS']='http://127.0.0.1:18134'
    directory = Path(sys.argv[1]); directory.mkdir(parents=True, exist_ok=True)
    app = create_app(directory/'data', gateway=Gateway(), run_background=False)
    r, c = app.state.runtime, app.state.config
    c.save(Settings(enabled=True, account_id='preview', allowed_chat_ids=['preview@chatroom']), 'fixture-key')
    r.gateway_state={'status':'logged_in','loggedInUser':'preview'}; r.account_id='preview'
    @app.post('/api/fixture/reset')
    async def reset_fixture():
        c.save(c.settings.model_copy(update={'send_auto_recovery_enabled':True}))
        r.safety.resume('preview'); r.safety.hold('preview','failure_circuit','演示：连续发送失败')
        r.store.insert({'key':'preview/example/1','account':'preview','chat':'preview@chatroom',
                        'sender':'演示用户','content':'演示消息','timestamp':time.time()})
        r.store.update('preview/example/1','uncertain',reply='演示回复',error='回执待核实')
        return {'ok':True}
    (directory/'preview.json').write_text(json.dumps({'port':18134, 'token':c.admin_token}))
    uvicorn.run(app, host='127.0.0.1', port=18134, log_level='error')
