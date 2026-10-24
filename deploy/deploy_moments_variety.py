"""Reuse scoped, backed-up core deployment without restarting native WeChat."""
import deploy_tieba_interest as deployment

deployment.FILES = {'whalegirl_linux/moments.py', 'whalegirl_linux/moments.js',
                    'whalegirl_linux/settings.py', 'MOMENTS.md'}
deployment.FEATURE = 'moments-variety'

if __name__ == '__main__':
    deployment.main()
