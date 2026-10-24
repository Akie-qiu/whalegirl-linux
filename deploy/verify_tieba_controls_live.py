"""Reconcile and deliver the existing share shown by the user, without a new test message."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import urllib.request

ROOT=Path('/opt/whalegirl-linux/current').resolve()
sys.path.insert(0,str(ROOT))
DATA=Path('/var/lib/whalegirl-linux/core')
JOBS=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/whalegirl-jobs')
IDENTITY='aa6c4fa857604c72a252348f8f07b0e1'


def api(route,body=None):
    headers={'Authorization':'Bearer '+(DATA/'admin.token').read_text().strip()}
    if body is not None:headers['Content-Type']='application/json'
    req=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        headers=headers,data=json.dumps(body).encode() if body is not None else None)
    return json.load(urllib.request.urlopen(req,timeout=20))


async def main():
    from whalegirl_linux.gateway import AgentGateway
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        found=db.execute('SELECT * FROM tieba_shares WHERE id=?',(IDENTITY,)).fetchone()
        if not found:raise RuntimeError('Source share not found')
        row=dict(found)
    config=api('config')
    if row['account']!=config['account_id'] or not config['tieba_enabled']:
        raise RuntimeError('Account or master switch changed; no send attempted')
    evidence={}
    if row['state']=='uncertain':
        original=JOBS/'79289013114541d2ae1c3f906e77a9b8.json'
        job=json.loads(original.read_text())
        if job.get('state') or Path(str(original)+'.claimed').exists() or job.get('text')!=row['text'] or job.get('chat')!=row['chat']:
            raise RuntimeError('Original send boundary is not proven; leave it for manual review')
        os.environ['WHALEGIRL_UI_JOBS_DIR']=str(JOBS)
        gateway=AgentGateway('http://127.0.0.1:6175',(DATA/'gateway.token').read_text().strip())
        try:
            messages=await gateway.messages(row['chat'],limit=500,after_time=row['sent_at']-30,before_time=row['sent_at']+120)
        finally:await gateway.close()
        if any(m.get('isSelf') is True and m.get('content','').strip()==row['text'].strip() for m in messages):
            raise RuntimeError('Source share already exists; not resending')
        evidence={'old_job_unclaimed':True,'old_send_never_reached':True,'matching_original_outbound':0}
        api('tieba/shares/'+IDENTITY,{'op':'not_received'})
        row['state']='failed'
    if row['state']=='failed':
        api('tieba/shares/'+IDENTITY,{'op':'retry'})
    until=time.monotonic()+55
    while True:
        share=next(r for r in api('tieba')['shares'] if r['id']==IDENTITY)
        if share['state'] in ('sent','failed','uncertain') or time.monotonic()>until:break
        await asyncio.sleep(1)
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        states=[r[0] for r in db.execute('SELECT state FROM outbound_actions WHERE inbox_key=? ORDER BY sent_at',('tieba:'+IDENTITY,))]
    result={'at':time.time(),'share_id':IDENTITY,'state':share['state'],'receipt':share['receipt'],
            'error':share['error'],'attempt_states':states,'wechat_status':api('state').get('gateway',{}).get('status'),
            'new_test_messages':0,**evidence}
    (Path('/opt/whalegirl-linux-assets')/'tieba-controls-acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':asyncio.run(main())
