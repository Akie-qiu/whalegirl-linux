"""Deploy the 1024px canvas limit using the rollback-safe core release flow."""
import sqlite3

import deploy_moments_recovery as rollout

rollout.DEPLOYMENT = 'image-limit-1024-20261109'
rollout.FILES = {
    'whalegirl_linux/image_canvas.py',
    'whalegirl_linux/images.py',
    'whalegirl_linux/images.js',
    'whalegirl_linux/settings.py',
    'whalegirl_linux/web.html',
    'tests/test_image_canvas.py',
}
rollout.TEST_MODULES = ('test_image_canvas', 'test_images', 'test_moments')
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
