"""Bounded synthetic chat replay and resource probe; no native gateway or live DB.

Run on the Linux host in a source-only sandbox, under a memory/CPU-limited unit.
Production configuration/credentials are read-only; the key stays in memory.
"""
import argparse
import asyncio
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import resource
import statistics
import sys
import threading
import time

os.environ['LOGURU_LEVEL']='ERROR'
CORE=Path('/var/lib/whalegirl-linux/core')
BOT='eval_bot';ROOM='eval_group@chatroom';OWNER='eval_owner';A='eval_a';B='eval_b'
NAMES={OWNER:'飘渺',A:'阿甲',B:'阿乙',BOT:'DeepSeek酱'}
STICKERS={'a'*32:'点头赞同，表示知道了','b'*32:'开心大笑，轻松调侃',
          'c'*32:'轻轻抱抱，安慰陪伴','d'*32:'无奈叹气'}
CASES=[
    {'id':'topic','title':'旧话题已经结束，开始聊草莓蛋糕',
     'seed':[(A,'那台电脑蓝屏已经修好了，驱动也换完了'),(BOT,'好啦，能正常用就行')],
     'turns':[(B,'今天做了草莓蛋糕，塌成草莓饼了','direct',1),
              (B,'至少不用切了','direct',1)]},
    {'id':'tired','title':'疲惫时想吐槽，随后说出具体烦恼',
     'seed':[(A,'终于下班了')],
     'turns':[(A,'今天真有点累，不想听建议，就想骂两句','direct',1),
              (A,'领导说五分钟的会，开了俩小时','direct',1)]},
    {'id':'others','title':'别人互相安排，随后明确邀请机器人参与',
     'seed':[(A,'阿乙，你到我楼下了吗')],
     'turns':[(B,'还有五分钟，阿甲你先下楼吧','natural',1),
              (OWNER,'鲸鱼，你觉得阿乙会不会又迷路','direct',1)]},
    {'id':'resolved','title':'技术问题已经解决，最后只回一个表情',
     'seed':[(A,'这个文件路径一直报错'),(BOT,'把路径两边的引号补上再试试')],
     'turns':[(A,'好了，这回真的能用了','direct',1),(A,'[点头表情]','natural',47)]},
    {'id':'banter','title':'熟人说最后一把，顺着话头调侃',
     'seed':[(OWNER,'今天要早睡，不熬了'),(B,'你上次也是这么说的')],
     'turns':[(OWNER,'就再打一把','direct',1),(OWNER,'最后一把已经打了一个小时了','direct',1)]},
    {'id':'preference','title':'当前临时选择优先于长期偏好',
     'seed':[(OWNER,'平时奶茶我都点无糖的')],
     'turns':[(OWNER,'今天想喝全糖奶茶，别又推无糖的','direct',1),
              (OWNER,'就今天破例，明天再说','direct',1)]},
]
CASES.append({'id':'topic_owner','title':'同一人的旧偏好与当前新话题',
    'seed':[(OWNER,'那台电脑蓝屏已经修好了，驱动也换完了'),(BOT,'好啦，能正常用就行')],
    'turns':[(OWNER,'今天做了草莓蛋糕，塌成草莓饼了','direct',1),
             (OWNER,'至少不用切了','direct',1)]})
TRAINING=[
    (A,'作业终于交了'),(B,'活着出来了'),(OWNER,'交了就歇会儿呗'),(A,'躺了'),
    (B,'这把零杠八'),(A,'对面得给你开工资'),(B,'算我上班了'),(OWNER,'工资分我一半'),
    (A,'刚下班，累得不想动'),(B,'那先歇会儿嘛'),(A,'老板说两句，讲了半小时'),(OWNER,'他的两句按章节算吧'),
    (B,'终于把设置弄好了'),(A,'好耶，收工'),(OWNER,'我先去洗澡'),(B,'去吧'),
    (A,'今天考砸了，有点难过'),(B,'过来，先歇一会儿'),(OWNER,'我又把饭煮成粥了'),(A,'省一道汤'),
    (B,'我买的手柄三个月没拆封'),(OWNER,'盒子都比你玩得久'),(A,'晚安啦'),(B,'睡吧，明天见'),
]


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf-8');path.chmod(0o600)


def memory():
    values={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if ':' in line}
    return {'available_mib':round(values['MemAvailable']/2**20,2),
            'swap_used_mib':round((values['SwapTotal']-values['SwapFree'])/2**20,2)}


class Samples:
    def __init__(self):self.rows=[];self.stop=threading.Event();self.thread=None
    def start(self):
        def run():
            while not self.stop.is_set():
                try:
                    status=Path('/proc/self/status').read_text()
                    rss=next(int(line.split()[1])/1024 for line in status.splitlines() if line.startswith('VmRSS:'))
                    self.rows.append({'at':time.time(),'rss_mib':round(rss,2),**memory()})
                except (OSError,ValueError):pass
                self.stop.wait(.5)
        self.thread=threading.Thread(target=run,daemon=True);self.thread.start()
    def close(self):self.stop.set();self.thread.join(2)


def production_inputs():
    from cryptography.fernet import Fernet
    settings=json.loads((CORE/'settings.json').read_text())
    profiles=json.loads((CORE/'model-profiles.json').read_text())
    active=settings.get('active_model_profile') or profiles.get('active') or 'custom'
    profile=profiles['profiles'][active]
    keyring=json.loads(Fernet((CORE/'encryption.key').read_bytes().strip()).decrypt((CORE/'model-api-keys.enc').read_bytes()))
    key=keyring.get(active)
    if not key:raise RuntimeError('selected_model_has_no_key')
    persona=settings.get('system_prompt_override') or ''
    return settings,profile,key,persona


class Gateway:
    def __init__(self):self.sent=[];self.store=None;self.next=5000
    async def status(self):return {'status':'logged_in','loggedInUser':BOT}
    status_for_send=status
    def warm_send_status(self):pass
    async def group_members(self,chat):return [{'id':key,'name':name} for key,name in NAMES.items()]
    async def chats(self):return []
    async def send(self,chat,text,**options):return await self.emit(chat,'text',text)
    async def send_sticker(self,chat,md5):return await self.emit(chat,'sticker',STICKERS.get(md5,'[表情]'),md5)
    async def emit(self,chat,kind,text,md5=''):
        from whalegirl_linux.gateway import Receipt
        self.sent.append({'type':kind,'text':text});self.next+=1
        row=event(self.next,BOT,text,chat=chat,kind=47 if md5 else 1)
        row['is_self']=1
        if md5:row['payload_json']=json.dumps({'sticker':{'md5':md5}})
        self.store.observe([row]);return Receipt(True,str(self.next))
    async def close(self):pass


def event(index,person,text,*,chat=ROOM,kind=1,trigger='direct',stamp=None):
    payload={'image_address':{'mentioned_me':True}} if trigger=='direct' else {}
    if kind==47:payload['sticker']={'md5':'a'*32}
    return {'key':f'{BOT}/{chat}/{index}','account':BOT,'chat':chat,'sender':NAMES.get(person,person),
            'sender_id':person,'is_self':int(person==BOT),'kind':kind,'local_id':index,'content':text,
            'trigger':trigger,'timestamp':time.time() if stamp is None else stamp,
            'payload_json':json.dumps(payload,ensure_ascii=False)}


def build(args,folder,*,real):
    from whalegirl_linux.runtime import Runtime
    from whalegirl_linux.settings import Config,Settings,reply_temperature
    from whalegirl_linux.store import Store
    from whalegirl_linux.upstream.ai_engine import AIEngine
    settings,profile,key,persona=production_inputs()
    # Functional controls only; voice, sampling and original persona stay equal.
    values={key:value for key,value in settings.items() if key in Settings.model_fields}
    values.update({key:value for key,value in profile.items() if key in Settings.model_fields})
    rooms=[ROOM] if real else [f'pressure-{index}@chatroom' for index in range(4)]
    values.update(enabled=True,account_id=BOT,allowed_chat_ids=rooms,owner_ids=[OWNER],
        system_prompt_override=persona,context_messages=args.context,
        min_send_interval=0,chat_cooldown=0,reply_part_interval=.3,debounce_single_seconds=0,
        debounce_burst_seconds=0,debounce_max_seconds=0,followup_wait_enabled=False,
        web_search_enabled=False,tieba_enabled=False,bilibili_enabled=False,
        images_chat_enabled=False,images_autosend=False,moments_enabled=False,
        proactive_enabled=False,trigger_only_chat_ids=[],safety_enabled=False,
        memory_consolidation_enabled=True,learning_enabled=False)
    if args.temperature is not None:values['model_temperature']=args.temperature
    if args.natural_mode:values['natural_group_replies']=True
    values={name:value for name,value in values.items() if name in Settings.model_fields}
    config=Config(folder);config.save(Settings.model_validate(values),'isolated-placeholder')
    config.api_key=lambda profile_id=None:key
    store=Store(folder/'state.sqlite3');gateway=Gateway();gateway.store=store
    calls=[]
    def factory(**options):
        options.update(timeout=40,max_retries=0)
        engine=AIEngine(**options)
        if not real:
            async def reply(messages,**kw):
                await asyncio.sleep(.002)
                return json.dumps({'should_reply':False,'actions':[]})
            engine.chat=reply
        original=engine.chat
        async def measured(messages,**kw):
            started=time.monotonic()
            answer=await original(messages,**kw)
            static=kw.get('system_prompt','').split('\n【本轮动态上下文】',1)[0]
            calls.append({'seconds':round(time.monotonic()-started,4),'static_hash':hashlib.sha256(static.encode()).hexdigest(),
                'system_chars':len(kw.get('system_prompt','')),'message_chars':len(json.dumps(messages,ensure_ascii=False)),
                'error':engine.last_error,'finish':engine.last_response_meta.get('finish_reason')})
            return answer
        engine.chat=measured;return engine
    runtime=Runtime(config,store,gateway,args.code/'styles',engine_factory=factory)
    runtime.stickers.entries=dict(STICKERS);runtime.stickers.available=list(STICKERS)
    runtime.account_id=BOT;runtime.gateway_state={'status':'logged_in','loggedInUser':BOT}
    return runtime,gateway,calls,{'model':config.settings.model,'temperature':reply_temperature(config.settings),
        'reasoning':config.settings.reasoning_effort,'persona_sha256':hashlib.sha256(persona.encode()).hexdigest(),
        'context_messages':args.context,'api_style':config.settings.api_style}


def seed(runtime,room,case=None,*,size=100):
    now=time.time();rows=[]
    for index in range(1,size+1):
        text=['昨天路过一家面包店，没进去','窗台的绿植又长了两片叶子','我把桌面整理了一下','天气还不错'][index%4]
        rows.append(event(index,A if index%2 else B,text,chat=room,stamp=now-1000+index*.1))
    for index,(person,text) in enumerate(TRAINING,300):
        rows.append(event(index,person,text,chat=room,stamp=now-700+(index-300)*4))
    # Style training and holdout subjects differ, but every arm gets the same data.
    runtime.store.observe(rows);runtime.fishtalk.rebuild(room)
    for index,(person,text) in enumerate((case or {}).get('seed',[]),700):
        runtime.store.observe([event(index,person,text,chat=room,stamp=now-30+index-700)])
    book=runtime.memory_book
    for person,content,kind,scope in [
        (OWNER,'喜欢折腾电脑驱动和修复蓝屏','preference','profile'),
        (OWNER,'平时奶茶喜欢无糖','preference','profile'),
        (A,'上个月反复讨论电脑蓝屏，最后已经解决','event','chat')]:
        row=book.change(BOT,runtime.config.settings.allowed_chat_ids,{'chat':room,'subject':person,
            'kind':kind,'scope':scope,'content':content,'source_key':''})
        with runtime.store.db:runtime.store.db.execute('UPDATE memories SET created=?,updated=? WHERE id=?',
            (now-15*86400,now-15*86400,row['id']))
    return rows


async def natural(args):
    case=next(row for row in CASES if row['id']==args.case)
    args.out.mkdir(parents=True,exist_ok=True,mode=0o700)
    runtime,gateway,calls,config=build(args,args.out,real=True)
    report={'case':case['id'],'title':case['title'],'code':str(args.code),'config':config,'turns':[],
            'synthetic':True,'native_sends':0,'training':'synthetic same-room corpus; holdout turns not learned'}
    report['config'].update(natural_group_replies=runtime.config.settings.natural_group_replies,
        autonomy_enabled=runtime.config.settings.autonomy_enabled,test_natural_mode=args.natural_mode)
    try:
        seed(runtime,ROOM,case)
        for index,(person,text,trigger,kind) in enumerate(case['turns'],1000):
            if memory()['available_mib']<96:raise RuntimeError('host_memory_guard')
            row=event(index,person,text,trigger=trigger,kind=kind)
            if kind==47:row['content']='[原生表情包] 表达：点头赞同'
            runtime.store.observe([row]);runtime.store.insert(row)
            before=len(gateway.sent);before_calls=len(calls);started=time.monotonic()
            async with runtime.chat_turn_lock:await runtime.process_next()
            state=runtime.store.get(row['key']) or {}
            raw=runtime.store.db.execute('SELECT metrics FROM reply_metrics WHERE key=?',(row['key'],)).fetchone()
            metrics=json.loads(raw[0]) if raw else {}
            report['turns'].append({'sender':NAMES[person],'input':row['content'],'trigger':trigger,
                'state':state.get('state'),'error':state.get('error',''),'sent':gateway.sent[before:],
                'elapsed_seconds':round(time.monotonic()-started,4),'calls':calls[before_calls:],
                'metrics':{key:metrics.get(key) for key in ('current_messages','recalled_messages','digest_included',
                    'long_term_memory','growth','fishtalk_reference','pre_model_seconds','model_seconds','sticker_pairing')}})
            write(args.out/'result.json',report)
        report['usage']=runtime.token_ledger.snapshot(BOT)
    finally:
        await runtime.close();write(args.out/'result.json',report)
    return report


def percentile(values,p):
    values=sorted(values)
    return round(values[min(len(values)-1,int((len(values)-1)*p))],5) if values else None


async def pressure(args):
    from whalegirl_linux.context import build_context
    args.out.mkdir(parents=True,exist_ok=True,mode=0o700)
    runtime,gateway,calls,config=build(args,args.out,real=False)
    report={'kind':'pressure','config':config,'external_model_calls':0,'native_sends':0,'phases':[]}
    try:
        rooms=runtime.config.settings.allowed_chat_ids
        for room in rooms:seed(runtime,room,size=500)
        times=[];counts=[]
        for index in range(60):
            if memory()['available_mib']<96:raise RuntimeError('host_memory_guard')
            room=rooms[index%4];row=event(1000+index,OWNER,'今天好累，就想吐槽两句',chat=room)
            runtime.store.observe([row]);started=time.monotonic()
            messages,_,info=await build_context(runtime.store,None,runtime.config.settings,row,[row],{},defer_unready=False)
            if 'batch' in inspect.signature(runtime.fishtalk.dynamic_prompt).parameters:
                runtime.fishtalk.dynamic_prompt(row,[row])
            else:runtime.fishtalk.dynamic_prompt(row)
            runtime.growth.prompt(row,[row])
            from whalegirl_linux.memory import memory_parts
            memory_parts(runtime.memory_book,runtime.config.settings,row,[row],diagnostics={})
            times.append(time.monotonic()-started);counts.append(info['current_messages'])
        report['phases'].append({'name':'60次100条上下文与记忆/文风预处理','rounds':len(times),
            'p50_seconds':percentile(times,.5),'p95_seconds':percentile(times,.95),'max_seconds':max(times),
            'min_context_messages':min(counts),'max_context_messages':max(counts)})
        burst=[];batch_started=time.monotonic()
        for index in range(48):
            row=event(2000+index,OWNER,'这一轮我只是想说说今天的事 '+str(index),chat=rooms[index%4])
            runtime.store.observe([row]);runtime.store.insert(row)
        max_parallel=0;running=0
        async def worker():
            nonlocal max_parallel,running
            while runtime.store.queue():
                if memory()['available_mib']<96:raise RuntimeError('host_memory_guard')
                async with runtime.chat_turn_lock:
                    running+=1;max_parallel=max(max_parallel,running)
                    started=time.monotonic()
                    try:await runtime.process_next()
                    finally:running-=1
                    burst.append(time.monotonic()-started)
        await asyncio.gather(worker(),worker())
        states={row[0]:row[1] for row in runtime.store.db.execute('SELECT state,count(*) FROM inbox GROUP BY state')}
        report['phases'].append({'name':'4群48条突发消息、两个竞争处理者复用真实会话锁','seconds':round(time.monotonic()-batch_started,4),
            'p50_round_seconds':percentile(burst,.5),'p95_round_seconds':percentile(burst,.95),
            'max_parallel':max_parallel,'actual_model_stub_calls':len(calls),'states':states,
            'pending':runtime.store.db.execute("SELECT count(*) FROM inbox WHERE state='queued'").fetchone()[0]})
        # Large authored messages stay complete, even past the diagnostic threshold.
        room=rooms[0];body='这是长正文中的实际内容，不应该因为字符阈值丢失。'*45
        for index in range(100):runtime.store.observe([event(3000+index,A,body,chat=room)])
        row=event(3200,A,'这段完整聊天都保留了吗',chat=room);runtime.store.observe([row])
        started=time.monotonic();messages,_,info=await build_context(runtime.store,None,runtime.config.settings,row,[row],{})
        rendered=messages[0]['content']
        report['phases'].append({'name':'100条高字数上下文','seconds':round(time.monotonic()-started,5),
            'context_messages':info['current_messages'],'truncated_messages':info['context_truncated_messages'],
            'characters':info.get('context_characters'),'complete_long_bodies':rendered.count(body),
            'over_character_threshold':info.get('context_over_character_threshold')})
    finally:
        report['peak_rss_mib']=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2)
        await runtime.close();write(args.out/'result.json',report)
    return report


async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--code',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--case',default='')
    parser.add_argument('--context',type=int,default=100);parser.add_argument('--temperature',type=float)
    parser.add_argument('--pressure',action='store_true');parser.add_argument('--natural-mode',action='store_true');args=parser.parse_args()
    sys.path.insert(0,str(args.code));logging.disable(logging.CRITICAL)
    from loguru import logger
    logger.remove();samples=Samples();samples.start();started=time.monotonic()
    report={}
    try:report=await (pressure(args) if args.pressure else natural(args))
    except Exception as error:
        existing=args.out/'result.json';report=json.loads(existing.read_text()) if existing.exists() else {}
        report['error_type']=type(error).__name__
        report['error_code']=str(error) if str(error)=='host_memory_guard' else 'isolated_test_incomplete'
        if hasattr(error,'errors'):
            report['validation_fields']=[{'field':list(item.get('loc',[])),'type':item.get('type')} for item in error.errors()]
        import traceback
        report['error_location']=[{'file':Path(item.filename).name,'line':item.lineno,'function':item.name}
            for item in traceback.extract_tb(error.__traceback__)[-4:]]
    finally:
        samples.close();report['process_seconds']=round(time.monotonic()-started,4)
        usage=resource.getrusage(resource.RUSAGE_SELF)
        report['cpu_seconds']=round(usage.ru_utime+usage.ru_stime,4)
        report['peak_rss_mib']=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2)
        report['resources']=samples.rows;write(args.out/'result.json',report)
        print(json.dumps({'case':args.case,'pressure':args.pressure,'out':str(args.out),
            'peak_rss_mib':report['peak_rss_mib'],'error':report.get('error_code','')},ensure_ascii=True),flush=True)


if __name__=='__main__':asyncio.run(main())
