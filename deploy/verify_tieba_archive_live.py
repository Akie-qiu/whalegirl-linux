"""Verify existing public source capture; no WeChat messages and no model requests."""
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import urllib.request

DATA = Path('/var/lib/whalegirl-linux/core')


def api(route, body=None):
    headers = {'Authorization': 'Bearer '+(DATA/'admin.token').read_text().strip()}
    raw = None if body is None else json.dumps(body).encode()
    if raw is not None:
        headers['Content-Type'] = 'application/json'
    with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8092/api/'+route, data=raw, headers=headers), timeout=15) as response:
        return json.load(response)


def main():
    config = api('config')
    account = config['account_id']
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro', uri=True) as db:
        row = db.execute('''SELECT p.tid FROM tieba_posts p JOIN tieba_pictures i ON i.tid=p.tid AND i.account=p.account
            WHERE p.account=? ORDER BY i.created DESC LIMIT 1''', (account,)).fetchone()
    if not row:
        print(json.dumps({'no_existing_picture_source': True}))
        return
    result = api('tieba/posts/'+row[0]+'/archive', {})
    identity = result['id']
    until = time.monotonic()+120
    while True:
        document = api('tieba/archives/'+identity)
        if document['state'] == 'complete':
            break
        if time.monotonic()>until:
            raise RuntimeError('Archive is still pending: '+document['state']+' '+document['error'])
        time.sleep(1)
    source = document['source']
    directory = DATA/'tieba-archives'/hashlib.sha256(account.encode()).hexdigest()[:24]/identity
    on_disk = json.loads((directory/'source.json').read_text())
    original = (directory/'original.txt').read_text()
    if on_disk != source or any(p['text'] not in original for p in source['posts']):
        raise RuntimeError('Source text mismatch')
    images = document['images']
    if len(images) != len(source['images']):
        raise RuntimeError('Missing source image records')
    for image in images:
        raw = (directory/(image['image_id']+'.bin')).read_bytes()
        if image['state'] != 'saved' or hashlib.sha256(raw).hexdigest() != image['sha256']:
            raise RuntimeError('Original image mismatch')
    state = api('state')
    result = {'at':time.time(), 'tid':source['tid'], 'archive_id':identity, 'state':document['state'],
              'text_preserved':True, 'text_characters':sum(len(p['text']) for p in source['posts']),
              'posts_read':len(source['posts']), 'main_floor_present':source['coverage']['main_floor_present'],
              'has_unread_later_floors':source['coverage']['has_more'], 'image_count':len(images),
              'images_saved':sum(p['state']=='saved' for p in images),
              'all_source_originals':all(p['original_available'] for p in images),
              'image_formats':list({p['mime'] for p in images}),
              'bytes_preserved':True, 'wechat_status':state.get('gateway',{}).get('status'),
              'wechat_test_messages_sent':0, 'ai_requests':0, 'archive_stats':api('tieba')['archive_stats']}
    (Path('/opt/whalegirl-linux-assets')/'tieba-archive-acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
