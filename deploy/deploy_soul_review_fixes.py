"""Use the validated core-only rollout with focused regression tests."""
import deploy_model_profiles as rollout

rollout.FILES = {
    'whalegirl_linux/conversation.py',
    'whalegirl_linux/autonomy.py',
    'whalegirl_linux/stickers.py',
    'tests/test_soul_upgrade_v5.py',
    'tests/test_soul_upgrade_regressions.py',
}
rollout.DEPLOYMENT = 'soul-review-fixes-20261031'


if __name__ == '__main__':
    rollout.main()
