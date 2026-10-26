"""Reuse the core-only publisher for exactly the three image-delivery files."""
import deploy_learning

deploy_learning.FILES = {'whalegirl_linux/'+name for name in ('image_tools.py', 'images.py', 'runtime.py')}
deploy_learning.DEPLOYMENT = 'image-delivery'

if __name__ == '__main__':
    deploy_learning.main()
