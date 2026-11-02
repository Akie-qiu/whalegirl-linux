"""Deploy the public fictional life world without restarting native WeChat."""
import sqlite3

import deploy_moments_recovery as rollout

rollout.DEPLOYMENT = 'moments-world-20261030'
rollout.FILES = {'whalegirl_linux/moments.py', 'whalegirl_linux/moments_creativity.py',
    'whalegirl_linux/images.py', 'whalegirl_linux/image_references.py', 'whalegirl_linux/app.py',
    'whalegirl_linux/moments.js', 'whalegirl_linux/web.html', 'whalegirl_linux/moments_world.py',
    'tests/test_moments_world.py', 'MOMENTS_WORLD_PLAN_20261030.md', 'MOMENTS.md'}
rollout.TEST_MODULES = ('test_moments_world', 'test_moments_story', 'test_moments',
    'test_moments_references', 'test_moments_recovery')
rollout.CONFIG_DEFAULTS = {}
original_inflight = rollout.inflight


def inflight():
    with sqlite3.connect('file:' + str(rollout.DATA / 'state.sqlite3') + '?mode=ro', uri=True) as db:
        return (original_inflight()
            + db.execute("SELECT count(*) FROM growth_runs WHERE state='planning'").fetchone()[0]
            + db.execute("SELECT count(*) FROM moments_actions WHERE state='generating'").fetchone()[0])


rollout.inflight = inflight
if __name__ == '__main__':
    rollout.REQUIRE_ONLINE = bool(rollout.api('config').get('enabled'))
    rollout.main()
