"""Inspect and reply once to an explicitly selected, already received authorized image."""
import argparse
import asyncio
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time

sys.path.insert(0,'/opt/whalegirl-linux/current')
from PIL import Image
from whalegirl_linux.context import build_context, vision_reply
from whalegirl_linux.gateway import AgentGateway
from whalegirl_linux.settings import Config
from whalegirl_linux.store import Store
from whalegirl_linux.upstream.ai_engine import AIEngine


async def run(chat,local_id):
    root=Path('/var/lib/whalegirl-linux/core');cfg=Config(root);settings=cfg.settings
    if chat not in settings.allowed_chat_ids:raise ValueError('Conversation is not authorized')
    os.environ['WHALEGIRL_UI_JOBS_DIR']='/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/whalegirl-jobs'
    gateway=AgentGateway('http://127.0.0.1:6175',cfg.gateway_token)
    # Attach only the store methods; do not run startup queue recovery in a second process.
    store=Store.__new__(Store);store.db=sqlite3.connect(root/'state.sqlite3');store.db.row_factory=sqlite3.Row;store.lock=threading.RLock()
    engine=AIEngine(settings.api_url,cfg.api_key(),model=settings.model,max_tokens=500,temperature=.3,max_retries=1,timeout=20)
    try:
        auth=await gateway.status()
        if auth.get('status')!='logged_in' or auth.get('loggedInUser')!=settings.account_id:raise ValueError('Authorized account is not logged in')
        first=store.db.execute('SELECT * FROM inbox WHERE account=? AND chat=? AND local_id=? ORDER BY timestamp DESC LIMIT 1',(settings.account_id,chat,local_id)).fetchone()
        if not first:raise ValueError('Selected incoming image was not recorded')
        first=dict(first);source=store.db.execute('SELECT * FROM message_log WHERE account=? AND chat=? AND local_id=?',(settings.account_id,chat,local_id)).fetchone()
        if not source or source['is_self']!=0 or source['kind']!=3:raise ValueError('Selected source is not an incoming image')
        labels={c['id']:c.get('remark') or c.get('name') or c['id'] for c in await gateway.chats()}
        start=time.monotonic();messages,keys,counts=await build_context(store,gateway,settings,first,[first],labels)
        if keys!=[source['key']]:raise ValueError('The selected image was not attached; no reply sent')
        context_seconds=round(time.monotonic()-start,3);tick=time.monotonic()
        reply=await engine.chat(messages,system_prompt='你是鲸鱼娘。现在更正此前没有读取这张图的情况，简短自然地描述此次实际附带的这张图片。不要将以前的图片当作新图，不要根据微信来源猜图片内是什么应用，文字看不清就明确说看不清。只输出JSON：{"reply":"发给用户的中文回复","image_descriptions":[{"key":"图片来源key","description":"实际可见内容的准确简述"}]}。')
        if not reply:raise ValueError(engine.last_error)
        model_seconds=round(time.monotonic()-tick,3);reply=vision_reply(reply,keys,store).strip()[:1500]
        tick=time.monotonic();receipt=await gateway.send(chat,reply);send_seconds=round(time.monotonic()-tick,3)
        cached=store.cached_image(source['key']);im=Image.open(BytesIO(base64.b64decode(cached['data'])))
        report={'chat':chat,'source_local_id':local_id,'source_key':source['key'],'attached_image_keys':keys,
                'context':counts,'context_seconds':context_seconds,'model_seconds':model_seconds,
                'send_readback_seconds':send_seconds,'processing_seconds':round(time.monotonic()-start,3),
                'confirmed':receipt.confirmed,'receipt':receipt.message_id,'error':receipt.error,
                'reply':reply,'image_quality':cached.get('quality'),'image_dimensions':list(im.size),'verified_at':time.time()}
        path=Path('/opt/whalegirl-linux-assets/candidate-4.1.1.8/context-vision-speed/current-image-acceptance.json');path.write_text(json.dumps(report,ensure_ascii=False,indent=2));path.chmod(0o600)
        print(json.dumps(report,ensure_ascii=False),flush=True)
    finally:
        await engine.close();await gateway.close();store.db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--chat',required=True);parser.add_argument('--local-id',type=int,required=True);args=parser.parse_args()
    asyncio.run(run(args.chat,args.local_id))
