"""Deploy public story creativity only; retain native login and all chat settings."""
import sqlite3

import deploy_moments_recovery as rollout

rollout.DEPLOYMENT = 'moments-story-20261030'
rollout.FILES = {'whalegirl_linux/moments.py', 'whalegirl_linux/images.py',
    'whalegirl_linux/image_references.py', 'whalegirl_linux/moments_creativity.py',
    'tests/test_moments_story.py', 'MOMENTS.md', 'MOMENTS_STORY_PLAN_20261030.md'}
rollout.TEST_MODULES = ('test_moments_story', 'test_moments', 'test_moments_references')
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
