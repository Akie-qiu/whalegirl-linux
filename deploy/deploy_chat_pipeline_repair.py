"""Core-only deployment for late photos, Tieba queues and empty model responses."""
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='chat-pipeline-repair'
rollout.FILES={'whalegirl_linux/context.py','whalegirl_linux/runtime.py','whalegirl_linux/gateway.py',
    'whalegirl_linux/proactive.py','whalegirl_linux/tieba.py','whalegirl_linux/upstream/ai_engine.py',
    'whalegirl_linux/tieba.js','whalegirl_linux/web.html','tests/test_chat_pipeline_repair.py'}
rollout.TEST_MODULES=('test_chat_pipeline_repair','test_incoming_image_wait','test_cache_layout_repair')


if __name__=='__main__':rollout.main()
