"""Core-only manual preference rollout, preserving WeChat and existing switches."""
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='sticker-preferences'
rollout.FILES={'whalegirl_linux/'+n for n in ('settings.py','conversation.py','app.py','proactive.js','web.html')}|{'tests/test_sticker_preferences.py'}
rollout.CONFIG_DEFAULTS={'sticker_text_pair_eagerness':90,'sticker_only_eagerness':75,'sticker_reaction_eagerness':90,
                        'sticker_max_per_reply':1,'sticker_style_note':''}
rollout.TEST_MODULES=('test_sticker_preferences','test_unified_actions','test_chat_control')
rollout.REQUIRE_ONLINE=False

if __name__=='__main__':rollout.main()
