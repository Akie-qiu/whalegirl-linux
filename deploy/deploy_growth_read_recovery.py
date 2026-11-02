import deploy_moments_recovery as rollout
rollout.DEPLOYMENT='growth-read-recovery'
rollout.FILES={'whalegirl_linux/growth.py','tests/test_autonomous_growth.py'}
rollout.TEST_MODULES=('test_autonomous_growth','test_cache_layout_repair')
if __name__=='__main__':rollout.main()
