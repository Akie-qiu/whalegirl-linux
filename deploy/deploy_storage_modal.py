"""Publish recovery receipt handling without restarting native WeChat."""
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='storage-modal'
rollout.FILES={'whalegirl_linux/gateway.py','deploy/native_recovery.py',
               'tests/test_storage_modal_recovery.py','tests/test_ui_job_completion.py'}
rollout.TEST_MODULES=('test_storage_modal_recovery','test_ui_job_completion','test_ui_recovery',
                     'test_independent_search','test_cache_layout_repair')
rollout.REQUIRE_ONLINE=False


if __name__=='__main__':rollout.main()
