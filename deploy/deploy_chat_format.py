"""Scoped chat-format rollout; retain native WeChat and all configuration."""
import sqlite3
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT = 'chat-format-20261031'
rollout.FILES = {'whalegirl_linux/conversation.py', 'whalegirl_linux/runtime.py',
    'tests/test_chat_format_repair.py', 'CHAT_FORMAT_REPAIR_20261031.md'}
rollout.TEST_MODULES = ('test_chat_format_repair', 'test_chat_habits', 'test_conversation_upgrade',
    'test_context_delivery', 'test_media_delivery')
rollout.CONFIG_DEFAULTS = {}
original_inflight = rollout.inflight


def inflight():
    with sqlite3.connect('file:' + str(rollout.DATA / 'state.sqlite3') + '?mode=ro', uri=True) as db:
        return original_inflight() + sum(db.execute(sql).fetchone()[0] for sql in (
            "SELECT count(*) FROM growth_runs WHERE state='planning'",
            "SELECT count(*) FROM moments_actions WHERE state='generating'"))


rollout.inflight = inflight
if __name__ == '__main__':
    rollout.REQUIRE_ONLINE = bool(rollout.api('config').get('enabled'))
    rollout.main()
