"""Core-only latency followup; retain WeChat, reader, settings and credentials."""
import sqlite3
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='reply-latency-phase3'
rollout.FILES={'whalegirl_linux/runtime.py','whalegirl_linux/store.py','whalegirl_linux/model_reply.py',
    'tests/test_direct_text_decision.py','tests/test_receive_timing.py','tests/test_reply_phase3.py'}
rollout.TEST_MODULES=('test_reply_phase3','test_direct_text_decision','test_receive_timing',
    'test_model_format_guard','test_reply_pipeline',
    'test_chat_habits.ChatHabitTests.test_mixed_native_plan_counts_each_message_once',
    'test_chat_habits.ChatHabitTests.test_followup_tool_keeps_original_burst_and_returns_result_to_model')
rollout.CONFIG_DEFAULTS={}
original_inflight=rollout.inflight


def inflight():
    with sqlite3.connect('file:'+str(rollout.DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        return original_inflight()+db.execute("SELECT count(*) FROM growth_runs WHERE state='planning'").fetchone()[0]


rollout.inflight=inflight
if __name__=='__main__':
    rollout.REQUIRE_ONLINE=bool(rollout.api('config').get('enabled'))
    rollout.main()
