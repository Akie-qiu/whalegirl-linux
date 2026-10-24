"""Retry one explicitly selected pre-submit failure through the normal console API."""
import argparse
import json
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.request

DATA=Path('/var/lib/whalegirl-linux/core')
ASSETS=Path('/opt/whalegirl-linux-assets')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--failed-id',required=True)
    args=parser.parse_args()
    token=(DATA/'admin.token').read_text().strip()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(route,body=None):
        deadline=time.monotonic()+90
        while True:
            req=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
                data=None if body is None else json.dumps(body).encode(),
                headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            try:return json.load(opener.open(req,timeout=70))
            except urllib.error.HTTPError as exc:
                reason=str(json.load(exc).get('detail',''))
                if exc.code==409 and '聊天正在处理' in reason and time.monotonic()<deadline:
                    time.sleep(.7);continue
                raise RuntimeError(reason[:240]) from None
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        row=db.execute('SELECT id,kind,state,payload,text FROM moments_actions WHERE id=?',(args.failed_id,)).fetchone()
        if row is None:raise RuntimeError('Selected action does not exist')
        old=dict(row)
    if old['kind']!='reply_own' or old['state']!='failed' or not old['text'].strip():
        raise RuntimeError('Only a selected failed own-comment reply may be repaired')
    source=json.loads(old['payload']);post_id=source['post']['id'];comment_id=source['comment']['id']
    attempt=ASSETS/('moments-repair-'+old['id']+'.json')
    if attempt.exists():
        identity=json.loads(attempt.read_text())['action_id']
    else:
        draft=api('moments/actions/'+old['id'],{'op':'retry'})
        identity=draft['id'];row=next(r for r in draft['actions'] if r['id']==identity)
        if row['state']!='draft':raise RuntimeError('No new draft; no send attempted')
        attempt.write_text(json.dumps({'at':time.time(),'original_id':old['id'],'action_id':identity,
            'post_id':post_id,'comment_id':comment_id}))
    row=next(r for r in api('moments')['actions'] if r['id']==identity)
    print(json.dumps({'repair_action':identity,'state':row['state']}),flush=True)
    started=time.monotonic()
    if row['state']=='draft':
        api('moments/actions/'+identity,{'op':'edit','text':old['text']})
        result=api('moments/actions/'+identity,{'op':'publish'})
        row=next(r for r in result['actions'] if r['id']==identity)
    result={'at':time.time(),'original_id':old['id'],'action_id':identity,'post_id':post_id,
            'comment_id':comment_id,'state':row['state'],'receipt':row.get('receipt'),
            'error':row.get('error'),'send_seconds':round(time.monotonic()-started,2)}
    (ASSETS/('moments-repair-'+old['id']+'-result.json')).write_text(json.dumps(result,indent=2))
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
