"""Scoped core rollout; retain native WeChat, login and all settings."""
import sqlite3
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT = 'chat-world-receipts-20261030'
rollout.FILES = {'whalegirl_linux/images.py', 'whalegirl_linux/image_tools.py',
    'whalegirl_linux/image_references.py', 'whalegirl_linux/moments_world.py', 'whalegirl_linux/delivery.py',
    'tests/test_chat_world_images.py', 'tests/test_picture_receipts.py', 'CHAT_WORLD_RECEIPT_FIX_20261030.md'}
rollout.TEST_MODULES = ('test_chat_world_images', 'test_picture_receipts', 'test_image_canvas',
    'test_image_tools', 'test_moments_world', 'test_context_delivery')
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
