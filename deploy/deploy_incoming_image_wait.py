"""Roll out image-transfer waiting with only a core restart."""
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='incoming-image-wait'
rollout.FILES={'whalegirl_linux/context.py','whalegirl_linux/runtime.py',
               'whalegirl_linux/console.js','whalegirl_linux/web.html',
               'tests/test_incoming_image_wait.py'}
rollout.TEST_MODULES=('test_incoming_image_wait','test_media_delivery','test_cache_layout_repair')


if __name__=='__main__':rollout.main()
