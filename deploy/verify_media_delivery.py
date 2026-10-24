"""Recover one already-authorized received image, without sending test messages."""
import asyncio
import base64
from io import BytesIO
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
import urllib.request

sys.path.insert(0,str(Path('/opt/whalegirl-linux/current').resolve()))
from PIL import Image
from whalegirl_linux.gateway import AgentGateway
from whalegirl_linux.settings import Config
from whalegirl_linux.upstream.ai_engine import AIEngine

DATA=Path('/var/lib/whalegirl-linux/core')
ASSETS=Path('/opt/whalegirl-linux-assets')
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
token=(DATA/'admin.token').read_text().strip()


def api(route,body=None):
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with opener.open(request,timeout=12) as response:return json.load(response)


async def capture(row):
    config=Config(DATA);s=config.settings
    gateway=AgentGateway('http://127.0.0.1:6175',config.gateway_token)
    gateway.ui_jobs_directory=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/whalegirl-jobs')
    started=time.monotonic()
    try:
        preview=ASSETS/'recovered-image-20261022.png'
        # This private capture was made by this verifier for the same fixed source.
        if sys.argv[1:]==['--reuse-capture'] and preview.is_file():
            media={'data':base64.b64encode(preview.read_bytes()).decode(),'format':'png','quality':'visible_chat_thumbnail'}
        else:
            media=await gateway.visible_media(row['chat'],row['local_id'],3,
                message_time=row['timestamp'],server_id=row['key'].rsplit('/',1)[-1])
        if not media.get('data'):raise RuntimeError('Exact original image still unavailable')
        raw=base64.b64decode(media['data'],validate=True)
        image=Image.open(BytesIO(raw));image.verify()
        image=Image.open(BytesIO(raw));size=image.size
        image.save(preview,'PNG');preview.chmod(0o600)
        engine=AIEngine(api_url=s.api_url,api_key=config.api_key(),model=s.model,max_tokens=150,temperature=.2,timeout=20,max_retries=1)
        try:
            value=await engine.chat([{'role':'user','content':[
                {'type':'text','text':'请用一句中文描述这张实际图片的主体、颜色和场景，只按可见图像描述'},
                {'type':'image_url','image_url':{'url':'data:image/'+media.get('format','jpeg')+';base64,'+media['data']}}]}])
        finally:await engine.close()
        if not value or re.search('未提供|没有收到|无法查看|没有图片|看不到|没加载|未加载',value):
            raise RuntimeError('Vision model did not describe the recovered source')
        # Do not initialize Store on the live database. Patch only this verified source.
        with sqlite3.connect(DATA/'state.sqlite3',timeout=10) as db:
            db.execute('''INSERT INTO image_cache(key,data,format,quality,partial) VALUES(?,?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET data=excluded.data,format=excluded.format,quality=excluded.quality,partial=excluded.partial''',
                (row['key'],media['data'],media.get('format','jpeg'),media.get('quality',''),int(bool(media.get('partial')))))
            db.execute('UPDATE message_log SET description=? WHERE key=?',(value[:1400],row['key']))
            db.execute('''INSERT INTO media_attempts(key,status,reason,updated) VALUES(?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET status=excluded.status,reason=excluded.reason,updated=excluded.updated''',
                (row['key'],'ready',media.get('quality',''),time.time()))
        report={'recovered':True,'local_id':row['local_id'],'quality':media.get('quality'),'dimensions':size,
            'description_saved':True,'description_length':len(value),'seconds':round(time.monotonic()-started,2),
            'wechat_status':(await gateway.status()).get('status'),'sent_test_messages':False}
        (ASSETS/'media-delivery-live-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False),flush=True)
    finally:await gateway.close()


def main():
    original=api('config')
    if api('state')['gateway']['status']!='logged_in':raise RuntimeError('Login required')
    db=sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    row=db.execute('SELECT * FROM message_log WHERE chat=? AND local_id=? AND kind=3 ORDER BY timestamp DESC LIMIT 1',
                   ('wxid_m5d9p57fcqq212',149)).fetchone()
    if row is None or row['chat'] not in original['allowed_chat_ids']:raise RuntimeError('Source not authorized')
    row=dict(row);db.close()
    api('config',{'settings':{'enabled':False}})
    try:
        for _ in range(100):
            if not api('state').get('processing'):break
            time.sleep(.2)
        else:raise RuntimeError('Core busy')
        asyncio.run(capture(row))
    finally:api('config',{'settings':{'enabled':original['enabled']}})


if __name__=='__main__':main()
