"""Verify expression through the full normal reply worker, with an isolated inbox."""
from verify_sticker_initiative_live import *


async def main():
    live=Config(DATA);s=live.settings
    if TARGET not in s.allowed_chat_ids:raise RuntimeError('Test chat not authorized')
    await idle(live)
    out=Path('/opt/whalegirl-linux-assets')/('sticker-turn-test-'+uuid.uuid4().hex[:12]);out.mkdir(mode=0o700)
    config=Config(out);config.settings=s.model_copy(update={'allowed_chat_ids':[TARGET],
        'reply_max_parts':2,'sticker_max_per_reply':1,'autonomy_enabled':False,
        'debounce_single_seconds':0,'debounce_burst_seconds':0,'debounce_max_seconds':0})
    config.api_key=live.api_key
    store=Store(out/'state.sqlite3');source=sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True);source.row_factory=sqlite3.Row
    os.environ['WHALEGIRL_UI_JOBS_DIR']='/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/whalegirl-jobs'
    gateway=AgentGateway('http://127.0.0.1:6175',live.gateway_token,control_directory=DATA)
    runtime=Runtime(config,store,gateway,ROOT/'styles');report={'target':'临时测试','messages_confirmed':0,'release':str(ROOT)}
    try:
        catalog=StickerCatalog(DATA);await catalog.refresh_available(gateway)
        catalog.disabled={r[0] for r in source.execute('SELECT md5 FROM sticker_library WHERE account=? AND enabled=0',(s.account_id,))}
        excluded={r[0] for r in source.execute('SELECT md5 FROM sticker_chat_library WHERE account=? AND chat=? AND enabled=0',(s.account_id,TARGET))}
        excluded.update(Store.used_stickers(SimpleNamespace(db=source),s.account_id,TARGET,s.sticker_cooldown_seconds))
        runtime.stickers=catalog;runtime.sticker_library.catalog=catalog
        runtime.sticker_library.excluded=lambda account,chat:excluded
        guard=object.__new__(SafetyGuard);guard.store=SimpleNamespace(db=source)
        runtime.safety.check=guard.check;runtime.safety.held=guard.held
        runtime.gateway_state=await gateway.status();runtime.account_id=s.account_id
        first={'key':'maintenance:'+uuid.uuid4().hex,'account':s.account_id,'chat':TARGET,
            'sender':'维护验收场景','sender_id':'maintenance-test','kind':1,'local_id':0,'is_self':0,
            'timestamp':time.time(),'trigger':'direct','content':'鲸鱼娘，被我逮到你又在偷偷摸鱼啦 哈哈',
            'payload_json':json.dumps({'maintenance_test':True,'isMentioned':True})}
        store.insert(first);store.observe([first]);started=time.monotonic()
        await runtime.process_next()
        row=store.get(first['key']);actions=store.actions(first['key'])
        report.update(state=row['state'],error=row['error'],seconds=round(time.monotonic()-started,3),
            messages_confirmed=sum(a['state']=='sent' for a in actions),
            actions=[{'kind':a['kind'],'state':a['state'],'receipt':a['receipt'],'error':a['error']} for a in actions],
            timing=runtime.last_timing)
    except Exception as exc:report['error']=str(exc)[:300];raise
    finally:
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k!='timing'},ensure_ascii=False),flush=True)
        source.close();await runtime.close()


if __name__=='__main__':asyncio.run(main())
