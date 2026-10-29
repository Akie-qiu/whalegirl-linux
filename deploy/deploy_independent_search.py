"""Core-only search credential isolation; preserve the live chat configuration."""
import json
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='independent-search'
rollout.FILES={'whalegirl_linux/'+name for name in ('search.py','settings.py','runtime.py','app.py','web.html','habits.js','console.js')}|{'tests/test_search.py','tests/test_independent_search.py'}
rollout.TEST_MODULES=('test_independent_search','test_search.SearchApiTests','test_search_clock.SearchDispatchTests',
                     'test_proactive_tools.ProactiveToolTests','test_cache_layout_repair')
rollout.CONFIG_DEFAULTS={'search_api_key_configured':(rollout.DATA/'search-api-key.enc').is_file()}
rollout.REQUIRE_ONLINE=False


if __name__=='__main__':
    saved=rollout.digest(rollout.DATA/'search-api-key.enc')
    rollout.main()
    if saved!=rollout.digest(rollout.DATA/'search-api-key.enc'):raise RuntimeError('Search credential changed during rollout')
    report=rollout.ASSETS/'independent-search-deployment.json'
    value=json.loads(report.read_text());value['preserved']['independent_search_key']=True
    report.write_text(json.dumps(value,ensure_ascii=False,indent=2))
