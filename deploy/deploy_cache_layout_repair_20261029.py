"""Core-only cache layout repair; no configuration, client or chat changes."""
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='cache-layout-repair'
rollout.FILES={'whalegirl_linux/'+n for n in ('time_context.py','upstream/ai_engine.py','memory.py','runtime.py',
    'moments.py','stickers.py','sticker_library.py')}|{'tests/test_cache_layout_repair.py','deploy/cache_operation_tags.py'}
rollout.TEST_MODULES=('test_cache_layout_repair','test_search_clock.ClockTests','test_unified_actions','test_sticker_preferences')
rollout.REQUIRE_ONLINE=False

if __name__=='__main__':rollout.main()
