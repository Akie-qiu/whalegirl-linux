"""Publish proactive tool parity without changing permissions or native WeChat."""
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='proactive-tools'
rollout.FILES={'whalegirl_linux/proactive.py','whalegirl_linux/proactive_tools.py',
               'whalegirl_linux/tieba_tools.py','tests/test_proactive_tools.py'}
rollout.TEST_MODULES=('test_proactive_tools','test_proactive','test_search_clock')
rollout.REQUIRE_ONLINE=False

if __name__=='__main__':rollout.main()
