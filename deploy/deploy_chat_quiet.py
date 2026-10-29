"""Core-only deployment; no native restart, login request or trial messages."""
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='chat-quiet'
rollout.FILES={'whalegirl_linux/chat_control.py','tests/test_chat_control.py'}|{'whalegirl_linux/'+n for n in (
    'runtime.py','settings.py','proactive.py','images.py','tieba.py','app.py',
    'console.js','habits.js','improvements.js','proactive.js','web.html')}
rollout.TEST_MODULES=('test_chat_control','test_proactive_tools','test_unified_actions')
rollout.CONFIG_DEFAULTS={'quiet_default_minutes':30}
rollout.REQUIRE_ONLINE=False

if __name__=='__main__':rollout.main()
