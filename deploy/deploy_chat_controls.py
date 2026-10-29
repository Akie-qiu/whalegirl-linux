"""Preserve all chat settings while publishing the consolidated console."""
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='chat-controls'
rollout.FILES={'whalegirl_linux/'+name for name in ('app.py','console.js','habits.js','proactive.js','web.html')}
rollout.FILES.add('tests/test_autonomy.py')
rollout.TEST_MODULES=('test_autonomy',)
rollout.REQUIRE_ONLINE=False  # UI publication need not wait for the sticker cache.

if __name__=='__main__':rollout.main()
