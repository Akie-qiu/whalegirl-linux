"""Two synthetic conversations with the configured model; no WeChat/image actions."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT=Path('/opt/whalegirl-linux/current').resolve();sys.path.insert(0,str(ROOT))
from whalegirl_linux.gateway import Receipt
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Config
from whalegirl_linux.stickers import parse_json
from whalegirl_linux.store import Store
from whalegirl_linux.upstream.ai_engine import AIEngine


class Gateway:
    def __init__(self):self.sent=[]
    async def status(self):return {'status':'logged_in','loggedInUser':'synthetic'}
    async def send(self,chat,text,**kwargs):
        self.sent.append(text);return Receipt(True,str(len(self.sent)))
    async def send_sticker(self,*args):raise RuntimeError('No stickers available in isolated acceptance')
    async def close(self):pass


async def main():
    live=Config(Path('/var/lib/whalegirl-linux/core'))
    folder=Path(tempfile.mkdtemp(prefix='chat-personality-model-',dir='/opt/whalegirl-linux-assets'))
    folder.chmod(0o700);config=Config(folder)
    settings=live.settings.model_copy(update={'enabled':True,'account_id':'synthetic','allowed_chat_ids':['check@chatroom'],
        'trigger_only_chat_ids':[],'debounce_single_seconds':0,'debounce_burst_seconds':0,'debounce_max_seconds':0,
        'group_mentions_enabled':False,'quote_replies_enabled':False})
    config.save(settings,live.api_key());store=Store(folder/'isolated.sqlite3');gateway=Gateway()
    runtime=Runtime(config,store,gateway,ROOT/'styles',engine_factory=AIEngine)
    runtime.memory_book.change('synthetic',settings.allowed_chat_ids,{'chat':'check@chatroom','subject':'check-person','kind':'joke',
        'scope':'chat','content':'之前大家拿摸鱼开玩笑，大肥鱼说这是物种优势；适合轻松互损，不用于认真烦恼','source_key':''})
    engine=await runtime.model()
    provider=[]
    async def metadata(response):
        await response.aread()
        data=response.json();choice=(data.get('choices') or [{}])[0];message=choice.get('message') or {}
        provider.append({'finish_reason':choice.get('finish_reason'),'content_chars':len(message.get('content') or ''),
            'reasoning_chars':len(message.get('reasoning_content') or ''),'usage':data.get('usage')})
    client=await engine._get_client();client.event_hooks['response']=[metadata]
    answers=[];original=engine.chat
    async def capture(messages,**kwargs):
        answer=await original(messages,**kwargs);answers.append(answer);return answer
    engine.chat=capture
    results=[]
    cases=[('playful','你怎么天天摸鱼','物种优势','工资扣了，看你还摸不摸鱼'),
           ('serious','今天加班加到快散架了','今天这是被折腾够了','不是开玩笑 我现在真挺难受的 也不想说话')]
    try:
        for number,(name,earlier,own,text) in enumerate(cases):
            for offset,(content,is_self) in enumerate([(earlier,False),(own,True),(text,False)]):
                first=Runtime.event('synthetic','check@chatroom',{'serverId':100+number*10+offset,'localId':100+number*10+offset,
                    'sender':'synthetic' if is_self else 'check-person','senderName':'机器人' if is_self else '检查请求者',
                    'type':1,'isSelf':is_self,'content':content,'timestamp':time.time()-2+offset})
                store.observe([first])
            first['trigger']='direct';store.insert(first);started=time.monotonic();calls=len(answers)
            # Run the real context, prompt and response path; Gateway only records synthetic sends locally.
            await runtime.process_next();answer=answers[-1] if len(answers)>calls else ''
            (folder/(name+'-response.json')).write_text(json.dumps({'answer':answer,'provider':provider[-1:]},ensure_ascii=False,indent=2))
            row=store.get(first['key']);metrics=store.db.execute('SELECT metrics FROM reply_metrics WHERE key=?',(first['key'],)).fetchone()
            metrics=json.loads(metrics[0]) if metrics else {}
            results.append({'case':name,'seconds':round(time.monotonic()-started,3),'reply':row['reply'],
                'state':row['state'],'error':row['error'],'model_requests':len(answers)-calls,
                'joke_candidates':len(metrics.get('long_term_memory',{}).get('joke_ids',[]))})
        result={'cases':results,'usage':engine.usage.summary,'provider':provider,'production_chat_sent':False,'image_api_called':False}
        (folder/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        Path('/opt/whalegirl-linux-assets/chat-personality-model-acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False))
        if any(r['state']!='sent' for r in results):raise RuntimeError('Synthetic runtime acceptance incomplete')
    finally:
        await runtime.close()


if __name__=='__main__':asyncio.run(main())
