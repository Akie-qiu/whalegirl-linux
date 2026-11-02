import deploy_moments_recovery as rollout
rollout.DEPLOYMENT='growth-budget'
rollout.FILES={'whalegirl_linux/settings.py','whalegirl_linux/growth.js','whalegirl_linux/web.html'}
rollout.TEST_MODULES=('test_autonomous_growth','test_cache_layout_repair')
if __name__=='__main__':rollout.main()
