"""One model-owned expression plan, limited to the authorized 临时测试 group."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from types import SimpleNamespace
import urllib.request
import uuid

ROOT=Path('/opt/whalegirl-linux/current').resolve();sys.path.insert(0,str(ROOT))
from whalegirl_linux.conversation import habit_static_prompt,plan_actions,sticker_static_prompt,sticker_dynamic_prompt,sticker_usage_prompt
from whalegirl_linux.gateway import AgentGateway
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.safety import SafetyGuard
from whalegirl_linux.settings import Config
from whalegirl_linux.stickers import StickerCatalog,parse_json
from whalegirl_linux.store import Store
from whalegirl_linux.upstream.ai_engine import AIEngine

DATA=Path('/var/lib/whalegirl-linux/core');TARGET='56614122712@chatroom'
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path,token):
    request=urllib.request.Request('http://127.0.0.1:8092'+path,headers={'Authorization':'Bearer '+token})
    with OPENER.open(request,timeout=12) as response:return json.load(response)


async def idle(live):
    for _ in range(30):
        state=api('/api/state',live.admin_token)
        if state.get('paused_reason') or not state['enabled'] or state['gateway'].get('status')!='logged_in':raise RuntimeError('Live chat is not ready')
        if not state['processing']:return
        await asyncio.sleep(1)
    raise RuntimeError('Live chat is busy; no test sent')


async def main():
    live=Config(DATA);s=live.settings
    if TARGET not in s.allowed_chat_ids:raise RuntimeError('Test chat not authorized')
    await idle(live)
    out=Path('/opt/whalegirl-linux-assets')/('sticker-expression-test-'+uuid.uuid4().hex[:12]);out.mkdir(mode=0o700)
    config=Config(out);config.settings=s.model_copy(update={'reply_max_parts':2,'sticker_max_per_reply':1});config.api_key=live.api_key
    store=Store(out/'state.sqlite3');source=sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True);source.row_factory=sqlite3.Row
    os.environ['WHALEGIRL_UI_JOBS_DIR']='/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/whalegirl-jobs'
    gateway=AgentGateway('http://127.0.0.1:6175',live.gateway_token,control_directory=DATA)
    runtime=Runtime(config,store,gateway,ROOT/'styles');engine=None
    report={'target':'临时测试','release':str(ROOT),'messages_confirmed':0}
    try:
        if not any(c['id']==TARGET and c.get('name')=='临时测试' for c in await gateway.chats()):raise RuntimeError('Test chat identity changed')
        catalog=StickerCatalog(DATA);await catalog.refresh_available(gateway)
        catalog.disabled={r[0] for r in source.execute('SELECT md5 FROM sticker_library WHERE account=? AND enabled=0',(s.account_id,))}
        excluded={r[0] for r in source.execute('SELECT md5 FROM sticker_chat_library WHERE account=? AND chat=? AND enabled=0',(s.account_id,TARGET))}
        excluded.update(Store.used_stickers(SimpleNamespace(db=source),s.account_id,TARGET,s.sticker_cooldown_seconds))
        choices=catalog.candidates('哈哈 被逮到摸鱼啦 撒娇 害羞 可爱 开心',s.sticker_candidate_limit,excluded)
        if not choices:raise RuntimeError('No usable sticker choices')
        first={'key':'maintenance:'+uuid.uuid4().hex,'account':s.account_id,'chat':TARGET,'sender':'系统功能验收',
               'sender_id':'','kind':1,'local_id':0,'timestamp':time.time(),'trigger':'maintenance',
               'content':'合成验收场景：朋友笑着说“被我逮到你又在摸鱼啦”','payload_json':'{}'}
        stats={};feedback=sticker_usage_prompt(SimpleNamespace(db=source),s,first,stats)
        style=s.system_prompt_override.strip() or runtime.styles.get_active().system_prompt
        system=(style+habit_static_prompt(config.settings)+sticker_static_prompt(config.settings)
                +'\n管理员授权的表达验收，下面是合成场景，不写入人物记忆。像平时聊天自然接话，最多2个动作，表情只来自目录。输出JSON {"should_reply":true,"actions":[...]}，自主选择文字、表情及顺序。'
                +'\n【本轮动态上下文】'+sticker_dynamic_prompt(choices,catalog.details(choices))+feedback)
        engine=AIEngine(api_url=s.api_url,api_key=live.api_key(),model=s.model,api_style=s.api_style,
            reasoning_effort=s.reasoning_effort,stream_enabled=s.model_stream_enabled,max_tokens=max(1600,s.max_tokens),timeout=45,max_retries=0,temperature=.55)
        engine.usage_callback=lambda e:runtime.token_ledger.record(s.account_id,e)
        started=time.monotonic()
        result=await engine.chat([{'role':'user','content':first['content']}],system_prompt=system,max_tokens=max(1600,s.max_tokens),response_format={'type':'json_object'},operation='connection_test')
        report.update(model_returned_content=bool(result),model_error=engine.last_error,
            model_finish=getattr(engine,'last_response_meta',{}).get('finish_reason'))
        if not result:raise RuntimeError('Upstream returned no content; no actions sent')
        decision=parse_json(result or '');actions=plan_actions(decision,choices,config.settings)
        report.update(model_selected_types=[a['type'] for a in actions],generation_seconds=round(time.monotonic()-started,3),frequency=stats)
        if not actions:raise RuntimeError('Model chose silence; no fabricated fallback')
        await idle(live)
        guard=object.__new__(SafetyGuard);guard.store=SimpleNamespace(db=source)
        runtime.safety.check=guard.check;runtime.safety.held=guard.held
        store.insert(first);first['timestamp']=time.time()
        await runtime.send_plan(first,[first],first,actions,{},config.generation,{})
        row=store.get(first['key']);outcomes=store.actions(first['key'])
        report.update(state=row['state'],messages_confirmed=sum(a['state']=='sent' for a in outcomes),
            receipts=[{'kind':a['kind'],'state':a['state'],'receipt':a['receipt'],'error':a['error']} for a in outcomes],
            send_seconds=runtime.last_timing.get('send_readback_seconds'),config_unchanged=api('/api/config',live.admin_token)['sticker_usage_target']==s.sticker_usage_target)
        if row['state']!='sent':raise RuntimeError('Delivery not fully confirmed; no automatic test resend')
    except Exception as exc:
        report['error']=str(exc)[:250];raise
    finally:
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({**report,'report_path':str(out/'report.json')},ensure_ascii=False),flush=True)
        if engine:await engine.close()
        source.close();await runtime.close()


if __name__=='__main__':asyncio.run(main())
