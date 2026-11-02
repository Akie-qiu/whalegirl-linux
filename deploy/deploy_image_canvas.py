"""Core-only canvas release; retain native WeChat, runtime config and login."""
import sqlite3
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT = 'image-4k-ratio-20261030'
rollout.FILES = {'whalegirl_linux/images.py','whalegirl_linux/image_tools.py','whalegirl_linux/image_references.py',
    'whalegirl_linux/moments.py','whalegirl_linux/images.js','whalegirl_linux/app.py','whalegirl_linux/web.html','MOMENTS.md',
    'whalegirl_linux/image_canvas.py','tests/test_image_canvas.py','tests/test_image_tools.py','IMAGE_4K_RATIO_PLAN_20261030.md'}
rollout.TEST_MODULES = ('test_image_canvas','test_images','test_image_tools','test_moments_world','test_moments_story')
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
