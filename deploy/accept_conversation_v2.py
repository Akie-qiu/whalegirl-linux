"""Inspect live records and saved media without sending operator test messages."""
import asyncio
import base64
import json
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

BASE=Path('/opt/whalegirl-linux');DATA=Path('/var/lib/whalegirl-linux/core')
ASSETS=Path('/opt/whalegirl-linux-assets');OUT=ASSETS/'conversation-v2-acceptance'
OUT.mkdir(mode=0o700,exist_ok=True)
sys.path.insert(0,str(BASE/'current'))
token=(DATA/'admin.token').read_text().strip()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(route,body=None,timeout=12):
    req=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with opener.open(req,timeout=timeout) as r:return json.load(r)


def complete_manual_profiles():
    migrated=[]
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        rows=[dict(r) for r in db.execute("SELECT * FROM memories WHERE actor='admin' AND scope='chat' AND archived=0 AND kind IN ('fact','preference')")]
        for m in rows:
            known=db.execute('SELECT 1 FROM message_log WHERE account=? AND sender_id=? LIMIT 1',(m['account'],m['subject'])).fetchone()
            if (known and re.search(r'真名|外号|名字|喜欢|讨厌|生日|职业',m['content']) and
                not re.search(r'本群|群里|今天|昨天|刚才',m['content'])):
                result=api('memory',{'op':'update','id':m['id'],'version':m['version'],'scope':'profile'})
                migrated.append(result['id'])
    return migrated


async def saved_image_checks():
    # Store startup recovery is permitted only on this independent acceptance copy.
    clone=OUT/('state-copy-'+str(time.time_ns())+'.sqlite3')
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as live:
        with sqlite3.connect(clone) as copy:live.backup(copy)
    from whalegirl_linux.store import Store
    from whalegirl_linux.context import build_context
    from whalegirl_linux.settings import Settings
    store=Store(clone)
    raw=api('config');raw.pop('api_key_configured',None);settings=Settings.model_validate(raw)
    checked={'group_images_prepared':0,'quoted_images_prepared':0,'quoted_images_missing_source':0}
    try:
        rows=store.db.execute('''SELECT m.* FROM message_log m JOIN image_cache c ON m.key=c.key
            WHERE m.kind=3 AND m.chat LIKE '%@chatroom' ORDER BY m.timestamp DESC LIMIT 3''').fetchall()
        for row in rows:
            row=dict(row);media=store.cached_image(row['key']);raw=base64.b64decode(media['data'])
            if raw.startswith(b'\x89PNG'):mime='image/png'
            elif raw.startswith(b'\xff\xd8\xff'):mime='image/jpeg'
            elif raw.startswith((b'GIF87a',b'GIF89a')):mime='image/gif'
            elif raw[:4]==b'RIFF' and raw[8:12]==b'WEBP':mime='image/webp'
            else:raise RuntimeError('Cached group image format did not validate')
            _,keys,_=await build_context(store,None,settings,row,[row],{})
            if row['key'] not in keys:raise RuntimeError('Saved group image was not attached to its source')
            checked['group_images_prepared']+=1
        rows=store.db.execute("SELECT * FROM message_log WHERE kind=49 AND payload_json!='{}' ORDER BY timestamp DESC").fetchall()
        for row in rows:
            row=dict(row);ref=json.loads(row['payload_json']).get('reply') or {}
            if ref.get('type')!=3:continue
            source=store.find_message(row['account'],row['chat'],ref.get('serverId',''))
            if not source or not store.cached_image(source['key']):checked['quoted_images_missing_source']+=1;continue
            row['payload_json']=row['payload_json']
            _,keys,_=await build_context(store,None,settings,row,[row],{})
            if keys!=[source['key']]:raise RuntimeError('Actual quote attached a different image')
            checked['quoted_images_prepared']+=1
            if checked['quoted_images_prepared']>=3:break
        return checked
    finally:store.close()


def main():
    migrated=complete_manual_profiles()
    media=asyncio.run(saved_image_checks())
    for _ in range(9):
        catalog=api('stickers')
        if all(m in catalog.get('profiles',{}) for m in catalog['stickers']):break
        api('stickers/refresh',{},timeout=45)
        print(json.dumps({'semantic_profiles':len(api('stickers').get('profiles',{}))}),flush=True)
    state=api('state');catalog=api('stickers')
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        latest=[]
        for row in db.execute('''SELECT m.metrics,i.state FROM reply_metrics m JOIN inbox i ON m.key=i.key
                WHERE m.updated>? ORDER BY m.updated DESC LIMIT 12''',(time.time()-900,)):
            metrics=json.loads(row['metrics'])
            latest.append({'state':row['state'],**{k:metrics[k] for k in ('total_seconds','first_reply_seconds','model_seconds',
                'context_seconds','current_messages','recalled_messages','images','send_readback_seconds') if k in metrics}})
        result={'checked_at':time.time(),'manual_profiles_migrated':migrated,'saved_media':media,
            'stickers':{'available':len(catalog['stickers']),'detailed':len(catalog.get('profiles',{})),'error':catalog.get('error')},
            'digests':db.execute('SELECT count(*) FROM chat_digests').fetchone()[0],
            'memory_counts':[dict(r) for r in db.execute('SELECT scope,kind,count(*) AS n FROM memories WHERE archived=0 GROUP BY scope,kind')],
            'recent_replies':latest,'gateway':state['gateway']['status'],'enabled':state['enabled'],
            'paused':state['paused_reason'],'last_error':state['last_error']}
    (OUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
