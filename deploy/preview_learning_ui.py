"""Local learning UI fixture; no model calls, background tasks or real messages."""
import asyncio
from pathlib import Path
import sys
import time

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from whalegirl_linux.app import create_app
from whalegirl_linux.settings import Settings


class Gateway:
    login = {}
    inflight_sends = 0
    ui_jobs_directory = None
    ui_lock = asyncio.Lock()

    async def chats(self):
        return [{'id': 'preview@chatroom', 'name': '演示群聊', 'isGroup': True}]

    async def status(self):
        return {'status': 'disconnected'}

    async def close(self):
        pass


if __name__ == '__main__':
    app = create_app(Path(sys.argv[1]), gateway=Gateway(), run_background=False)
    config, runtime = app.state.config, app.state.runtime
    config.save(Settings(account_id='preview', allowed_chat_ids=['preview@chatroom'], learning_auto_accept=False))
    source = dict(key='preview/group/1', account='preview', chat='preview@chatroom',
                  sender='演示群友', sender_id='preview-person', kind=1, is_self=0, local_id=1,
                  content='我喜欢猫和安静聊天', timestamp=time.time(), trigger='direct')
    runtime.store.observe([source])
    runtime.learning.book.propose(source, [source], dict(
        type='preference', scope='profile', subject='preview-person', source_key=source['key'],
        evidence='我喜欢猫', content='喜欢猫', confidence=.95, ttl_days=30), config.settings)
    uvicorn.run(app, host='127.0.0.1', port=18118, log_level='error')
