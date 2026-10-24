"""Use the existing scoped deployment flow for source-excerpt sharing."""
import deploy_tieba_interest as deployment

deployment.FILES = {'whalegirl_linux/tieba.py', 'whalegirl_linux/tieba.js', 'TIEBA.md'}
deployment.FEATURE = 'tieba-excerpt'

if __name__ == '__main__':
    deployment.main()
