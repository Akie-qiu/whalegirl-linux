import sqlite3
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='http-relay-zero'
rollout.FILES={'tests/test_http_relay.py'}|{'whalegirl_linux/'+n for n in ('settings.py','upstream/ai_engine.py','console.js','web.html')}
rollout.TEST_MODULES=('test_http_relay','test_model_controls','test_cache_layout_repair')
original_inflight=rollout.inflight


def inflight():
    with sqlite3.connect('file:'+str(rollout.DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        growth=db.execute("SELECT count(*) FROM growth_runs WHERE state='planning'").fetchone()[0]
    return original_inflight()+growth


rollout.inflight=inflight
if __name__=='__main__':
    # A disabled bot intentionally has no loaded sticker catalog. Preserve
    # that setting and verify the pre-existing login without requiring one.
    rollout.REQUIRE_ONLINE=bool(rollout.api('config').get('enabled'))
    rollout.main()
