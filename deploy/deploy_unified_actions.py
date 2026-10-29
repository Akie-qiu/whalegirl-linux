"""Core-only rollout of unified actions; native login and permissions stay intact."""
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='unified-actions'
rollout.FILES={'whalegirl_linux/'+name for name in (
    'context.py','conversation.py','proactive.py','runtime.py','settings.py',
    'improvements.js','proactive.js','web.html')}|{
    'tests/test_unified_actions.py','tests/test_reply_expression.py','tests/test_autonomy.py'}
rollout.TEST_MODULES=('test_unified_actions','test_proactive_tools','test_reply_expression','test_proactive')
rollout.REQUIRE_ONLINE=False

if __name__=='__main__':rollout.main()
