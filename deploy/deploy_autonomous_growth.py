"""Core-only release; defaults leave Tieba growth reading disabled."""
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='autonomous-growth'
rollout.FILES={'whalegirl_linux/'+n for n in ('settings.py','memory_hub.py','memory.py','runtime.py',
    'proactive.py','app.py','web.html','console.js','token_usage.py','token-usage.js','memory_layers.py','growth.py','growth.js')}
rollout.FILES.add('tests/test_autonomous_growth.py')
rollout.TEST_MODULES=('test_autonomous_growth','test_cache_layout_repair')
rollout.CONFIG_DEFAULTS={'growth_enabled':True,'growth_web_enabled':True,'growth_tieba_enabled':False,
    'growth_auto_apply':True,'growth_daily_tokens':20000,'growth_reads_per_day':3,
    'growth_interval_minutes':180,'growth_exploration':25,'growth_interests':[]}


if __name__=='__main__':rollout.main()
