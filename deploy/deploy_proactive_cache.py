"""Core-only follow-up removing old proactive prefill and duplicate catalog."""
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='proactive-cache'
rollout.FILES={'whalegirl_linux/proactive.py','tests/test_proactive_cache.py'}
rollout.TEST_MODULES=('test_proactive_cache','test_proactive_tools','test_unified_actions')
rollout.REQUIRE_ONLINE=False

if __name__=='__main__':rollout.main()
