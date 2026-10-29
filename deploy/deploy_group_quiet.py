"""Core-only group quiet/wake rollout; preserve native WeChat and all credentials."""
import json
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='group-quiet'
rollout.FILES={'whalegirl_linux/chat_control.py','whalegirl_linux/runtime.py','whalegirl_linux/proactive.js',
               'whalegirl_linux/web.html','tests/test_chat_control.py','tests/test_silence_commands.py'}
rollout.TEST_MODULES=('test_chat_control','test_silence_commands','test_proactive_tools.ProactiveToolTests','test_cache_layout_repair')
rollout.REQUIRE_ONLINE=False


if __name__=='__main__':
    saved=rollout.digest(rollout.DATA/'search-api-key.enc')
    rollout.main()
    if saved!=rollout.digest(rollout.DATA/'search-api-key.enc'):raise RuntimeError('Search credential changed during rollout')
    report=rollout.ASSETS/'group-quiet-deployment.json'
    value=json.loads(report.read_text());value['preserved']['independent_search_key']=True
    report.write_text(json.dumps(value,ensure_ascii=False,indent=2))
