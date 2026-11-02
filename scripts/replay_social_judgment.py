"""Read-only production inputs, isolated runtime, fake WeChat gateway.

Loads the previously reviewed replay harness rather than duplicating the
application. Does not run its CLI, cleanup, deploy or native send methods.
All model credentials stay in memory; no original prompt goes into the report.
"""
import argparse
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch as mock_patch


def load(path, name):
    spec=importlib.util.spec_from_file_location(name, path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--code',type=Path,required=True)
    parser.add_argument('--harness',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--profile')
    parser.add_argument('--supplement-only',action='store_true')
    parser.add_argument('--focus',action='store_true',help='Short continuous interaction plus the six supplementary cases')
    parser.add_argument('--turns',default='',help='Comma-separated focus/supplement case IDs for a bounded recheck')
    args=parser.parse_args()
    selected=set(filter(None,args.turns.split(',')))
    args.out.mkdir(parents=True,exist_ok=True)
    harness_module=load(args.harness,'previous_replay_harness')
    revision=load(args.code/'whalegirl_linux/persona_revision.py','reviewed_persona_revision')
    settings,persona,profile,key,stickers=harness_module.production_inputs(args.profile)
    persona,revision_report=revision.patch(persona)
    options=SimpleNamespace(code=args.code,arm='judgment',out=args.out,fake_model=False)

    class Harness(harness_module.Harness):
        async def turn(self,label,rows,*,gap=40,retries=0):
            # A failed turn stays visible; do not replace it with a later success.
            result=await super().turn(label,rows,gap=gap,retries=0)
            self.completed=getattr(self,'completed',[])+[result]
            (args.out/'progress.json').write_text(json.dumps({'turns':self.completed},ensure_ascii=False),'utf-8')
            return result

    harness=Harness(options)
    with mock_patch('time.time',harness.clock):
        harness.build(settings,persona,profile,key,stickers)
        try:
            if args.supplement_only or args.focus:
                await harness.instrument()
                report={'turns':[]}
            else:
                report=await harness.scenario()
            owner,friend,other=harness_module.OWNER,harness_module.FRIEND,harness_module.FRIEND_B
            groups=['quiet_fixture@chatroom','invitation_fixture@chatroom','target_fixture@chatroom']
            sandbox_settings=harness.runtime.config.settings
            harness.runtime.config.save(sandbox_settings.model_copy(update={
                'allowed_chat_ids':list(dict.fromkeys([*sandbox_settings.allowed_chat_ids,*groups]))}))
            # Keep the existing instrumented engine despite an isolated settings save.
            harness.runtime.engine_generation=harness.runtime.config.generation
            def human(chat,sender,text,*,direct=False,data=None):
                row=harness.human(chat,sender,text,trigger='direct' if direct else 'natural')
                if data:row['payload_json']=json.dumps(data,ensure_ascii=False)
                return row
            if args.focus:
                for label,rows in [
                    ('F1 寻求陪聊',[human(harness_module.GROUP,owner,'今天有点累 只是想找你聊会儿',direct=True)]),
                    ('F2 亲近的斗嘴',[human(harness_module.GROUP,owner,'我才是你的妈妈 你别又管我啦',direct=True)]),
                    ('F3 群友调侃前因',[human(harness_module.GROUP,friend,'你这不就是妈系女儿')]),
                    ('F4 玩笑中闹别扭',[human(harness_module.GROUP,owner,'哼 不理你了',direct=True)]),
                ]:
                    if not selected or label.split()[0] in selected:
                        report['turns'].append(await harness.turn(label,rows,gap=45))
            cases=[
                ('S1 其他人之间的安排',[
                    human(groups[0],friend,'周六你几点到车站 我去接你',data={'reply':{'senderId':other,'content':'票买好了'}}),
                    human(groups[0],other,'下午三点到 等我出站给你电话')]),
                ('S2 邀请一起聊',[
                    human(groups[1],friend,'今天打游戏连续翻车 笑死 大家都是怎么缓过来的 鲸鱼你也说说',direct=True)]),
                ('S4 普通不同意见',[
                    human(harness_module.GROUP,owner,'我只是不同意你刚才的判断 不是生气也不用哄 你说说为什么这样想',direct=True)]),
                ('S5 原生引用的是别人',[
                    human(groups[2],friend,'到楼下了给我打电话 我下来',data={'reply':{'senderId':other,'content':'我快到了'}})]),
                ('S6 不编造天气观测',[
                    human(owner,owner,'你刚才说你那边云厚 你是真的看过天气还是顺着话说的',direct=True)]),
                ('S3 真实拒绝不是撒娇',[
                    human(owner,owner,'我现在真的不想被追问了 让我安静待会儿 不用安慰也不用回复',direct=True)]),
            ]
            for label,rows in cases:
                if not selected or label.split()[0] in selected:
                    report['turns'].append(await harness.turn(label,rows,gap=45))
        finally:
            await harness.runtime.close()
    harness_module.score(report)
    report.update(arm='judgment',model=profile.get('model'),fake_model=False,
                  persona_patch=revision_report,settings_policy='original production behavior preferences retained; only sandbox transport/tools disabled',
                  source_sha256={str(p.relative_to(args.code)):hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in (args.code/'whalegirl_linux').rglob('*.py')},
                  validation='synthetic conversation, real model, fake gateway; no native delivery or long-term acceptance')
    target=args.out/'replay-judgment.json'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    turns=report['turns']
    print(json.dumps({'written':str(target),'turns':len(turns),
                      'states':{state:sum(t.get('state')==state for t in turns) for state in {t.get('state') for t in turns}},
                      'model_calls':sum(len(t.get('model_calls',[])) for t in turns)},ensure_ascii=True))


if __name__=='__main__':
    asyncio.run(main())
