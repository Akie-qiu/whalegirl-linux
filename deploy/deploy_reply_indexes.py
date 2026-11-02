"""Run the existing Store migration on core start; WeChat stays untouched."""
import sqlite3
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='reply-indexes'
rollout.FILES={'whalegirl_linux/store.py'}
rollout.TEST_MODULES=('test_chat_habits.ChatHabitTests.test_mixed_native_plan_counts_each_message_once',
    'test_chat_habits.ChatHabitTests.test_sticker_allowance_does_not_consume_text_allowance',
    'test_media_delivery')
rollout.CONFIG_DEFAULTS={}
original_inflight=rollout.inflight


def inflight():
    with sqlite3.connect('file:'+str(rollout.DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        return original_inflight()+db.execute("SELECT count(*) FROM growth_runs WHERE state='planning'").fetchone()[0]


rollout.inflight=inflight
if __name__=='__main__':
    rollout.REQUIRE_ONLINE=bool(rollout.api('config').get('enabled'))
    rollout.main()
