"""Core/UI update with unchanged WeChat processes, login and credentials."""
import sqlite3
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT = 'world-document-20261031'
rollout.FILES = {'whalegirl_linux/moments_world.py', 'whalegirl_linux/app.py', 'whalegirl_linux/moments.js',
    'whalegirl_linux/console.js', 'whalegirl_linux/web.html', 'whalegirl_linux/image_references.py', 'whalegirl_linux/images.py',
    'whalegirl_linux/world_document.py', 'tests/test_world_document.py', 'WORLD_DOCUMENT_EDITOR_PLAN_20261031.md'}
rollout.TEST_MODULES = ('test_world_document', 'test_moments_world', 'test_chat_world_images', 'test_image_canvas', 'test_picture_receipts')
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
