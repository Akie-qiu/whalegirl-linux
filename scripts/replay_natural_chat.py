"""Bounded real-model comparison, synthetic messages, isolated DB/fake gateway.

Run each arm in a separate process. Production model inputs are read-only;
credentials stay in memory. Retain all test stores. Never run the old CLI.
"""
import argparse
import asyncio
import hashlib
import importlib.util
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def load(path):
    spec=importlib.util.spec_from_file_location('reviewed_harness',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--code',type=Path,required=True)
    parser.add_argument('--harness',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--arm',choices=['learning','baseline','compact','learned'],required=True)
    parser.add_argument('--notes',type=Path)
    parser.add_argument('--cases',default='',help='Comma-separated case IDs for bounded follow-up checks')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    module=load(args.harness)
    settings,persona,profile,key,stickers=module.production_inputs(None)
    # Same production model/temperature/thinking and 40-row history per arm.
    settings={**settings,'context_messages':40}
    learning_rows=[
        (module.FRIEND,'终于把作业交上去了'),(module.FRIEND_B,'活着出来了'),
        (module.OWNER,'交了就先歇会儿呗'),(module.FRIEND,'躺了'),
        (module.FRIEND_B,'这把我零杠八'),(module.FRIEND,'这战绩 对面不得给你发工资'),
        (module.FRIEND_B,'那我算上班了'),(module.OWNER,'工资记得分我一半'),
        (module.FRIEND,'搞定了 刚刚那个设置能用了'),(module.OWNER,'好耶'),
        (module.FRIEND_B,'收工'),(module.FRIEND,'嗯 我去洗澡'),
        (module.OWNER,'我这把差一点就赢了'),(module.FRIEND_B,'一点是多少'),
        (module.OWNER,'差一个能赢的我'),(module.FRIEND,'合理 我也缺一个'),
    ]
    notes=json.loads(args.notes.read_text('utf-8')).get('proposals',[]) if args.notes else []
    cases=[
        ('C1 吃饭后短回应',[(module.OWNER,'我去吃个饭'),('self','去吧 我先待着')],module.OWNER,'吃好了',False),
        ('C2 已接住后补表情',[(module.OWNER,'吃好了'),('self','那就歇会儿')],module.OWNER,'',True),
        ('C3 熟人玩笑',[(module.FRIEND,'你这把怎么打的'),(module.OWNER,'一打五')],module.OWNER,'不是对面五个 是队友加我',False),
        ('C4 别人互相安排',[(module.FRIEND,'周六你几点到车站 我去接你')],module.FRIEND_B,'下午三点到 等我出站给你电话',False),
        ('C5 明确技术请求',[],module.FRIEND,'帮我写个Python表达式 把列表里的空字符串去掉 保留数字0',False),
        ('C6 问题已经解决',[(module.FRIEND,'这个文件路径一直报错'),('self','把路径两边的引号补上再试试')],module.FRIEND,'已经好了 刚才照你说的改完就能用了',False),
    ]
    selected=set(filter(None,args.cases.split(',')))
    if selected:cases=[c for c in cases if c[0].split()[0] in selected]
    report={'arm':args.arm,'model':profile.get('model'),'turns':[],
            'validation':'synthetic fixtures; actual configured model; fake WeChat gateway; no native send'}
    for label,history,sender,text,is_sticker in ([('learning',[],module.FRIEND,'',False)] if args.arm=='learning' else cases):
        harness=module.Harness(SimpleNamespace(code=args.code,arm='natural',out=args.out,fake_model=False))
        with patch('time.time',harness.clock):
            harness.build(settings,persona,profile,key,stickers)
            try:
                harness.runtime.autonomy.sync_mode()
                sources=[]
                for who,content in learning_rows:
                    row=harness.human(module.GROUP,who,content)
                    row['timestamp']=harness.clock()-3600+len(sources)*8
                    sources.append(row)
                harness.store.observe(sources)
                if args.arm=='learning':
                    original_factory=harness.runtime.engine_factory
                    def factory(**options):
                        engine=original_factory(**options);chat=engine.chat
                        async def observed(messages,**kw):
                            data=json.loads(messages[0]['content'])
                            report['learning_input_counts']={k:len(v) for k,v in data.items() if isinstance(v,list)}
                            reply=await chat(messages,**kw);report['learning_reply']=reply
                            return reply
                        engine.chat=observed
                        return engine
                    harness.runtime.engine_factory=factory
                    started=time.monotonic()
                    report['ran']=await harness.runtime.autonomy.grow_once()
                    report['seconds']=round(time.monotonic()-started,3)
                    report['error']=harness.runtime.autonomy.last_error
                    report['proposals']=[dict(r) for r in harness.store.db.execute('''SELECT kind AS type,scope,subject,source_key,
                        evidence,content,confidence,state FROM learning_proposals ORDER BY id''')]
                    report['style']=harness.runtime.memory_hub.chat_style.snapshot(harness.runtime.config.settings,module.GROUP)
                    break
                if args.arm=='learned':
                    for item in notes:
                        harness.runtime.learning.book.propose(sources[-1],sources,item,harness.runtime.config.settings)
                for i,(who,content) in enumerate(history):
                    harness.clock.advance(5)
                    if who=='self':harness.self_row(module.GROUP,1,content)
                    else:harness.store.observe([harness.human(module.GROUP,who,content)])
                engine=await harness.runtime.model()
                original=engine.chat
                calls=[];usage=[]
                callback=engine.usage_callback
                def accounting(event):
                    usage.append(event)
                    if callback:callback(event)
                engine.usage_callback=accounting
                async def recorded(messages,**options):
                    start=time.monotonic();system=options.get('system_prompt','')
                    static=system.split('\n【本轮动态上下文】',1)[0]
                    result=await original(messages,**options)
                    calls.append({'seconds':round(time.monotonic()-start,3),'system_chars':len(system),'static_chars':len(static),
                        'static_sha256':hashlib.sha256(static.encode()).hexdigest(),
                        'message_chars':len(json.dumps(messages,ensure_ascii=False)),
                        'finish':(engine.last_response_meta or {}).get('finish_reason',''),
                        'reply':result,'error':engine.last_error})
                    return result
                engine.chat=recorded
                if is_sticker:
                    # Match what this image expresses, not an unrelated word
                    # mentioned in its avoid_when/profile descriptions.
                    md5=next((k for k,v in harness.catalog.items() if '点头赞同' in v),None)
                    if not md5:raise RuntimeError('Synthetic sticker fixture unavailable')
                    row=harness.human(module.GROUP,sender,'',kind=47,md5=md5,trigger='natural')
                else:
                    row=harness.human(module.GROUP,sender,text,trigger='natural' if label.startswith('C4') else 'direct')
                    if label.startswith('C4'):
                        row['payload_json']=json.dumps({'reply':{'senderId':module.FRIEND,'content':history[0][1]}},ensure_ascii=False)
                started=time.monotonic()
                result=await harness.turn(label,[row],gap=6,retries=0)
                state=harness.store.get(row['key']) or {}
                metric=harness.store.db.execute('SELECT metrics FROM reply_metrics WHERE key=?',(row['key'],)).fetchone()
                result.update(model_calls=calls,usage=usage,total_seconds=round(time.monotonic()-started,3),
                              sandbox=str(harness.sandbox),metrics=json.loads(metric['metrics']) if metric else {})
                result['text_chars']=sum(len(a['text']) for a in result['sent'] if a['type']=='text')
                result['actions']=len(result['sent'])
                report['turns'].append(result)
                (args.out/(args.arm+'-progress.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
            finally:
                await harness.runtime.close()
    target=args.out/(args.arm+'.json')
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({'arm':args.arm,'cases':len(report['turns']),'path':str(target),'ran':report.get('ran')},ensure_ascii=True))


if __name__=='__main__':asyncio.run(main())
