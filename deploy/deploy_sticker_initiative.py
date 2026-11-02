import sqlite3
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='sticker-initiative'
rollout.FILES={'whalegirl_linux/'+n for n in ('settings.py','conversation.py','runtime.py','proactive.py','app.py','proactive.js','web.html')}|{'tests/test_sticker_preferences.py','tests/test_unified_actions.py'}
rollout.CONFIG_DEFAULTS={'sticker_usage_target':0}
rollout.TEST_MODULES=('test_sticker_preferences','test_unified_actions','test_cache_layout_repair')
original_inflight=rollout.inflight


def inflight():
    with sqlite3.connect('file:'+str(rollout.DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        growth=db.execute("SELECT count(*) FROM growth_runs WHERE state='planning'").fetchone()[0]
    return original_inflight()+growth


rollout.inflight=inflight
if __name__=='__main__':rollout.main()
