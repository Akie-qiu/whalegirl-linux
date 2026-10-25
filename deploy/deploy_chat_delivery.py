"""Publish image matching, Moments queueing and proactive reply fixes."""
import deploy_responses

deploy_responses.KIND = 'chat-delivery'
deploy_responses.FILES = {'whalegirl_linux/'+name for name in (
    'images.py', 'image_tools.py', 'proactive.py', 'runtime.py',
    'moments.py', 'moments.js', 'app.py', 'web.html')}

if __name__ == '__main__':
    deploy_responses.main()
