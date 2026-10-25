"""Reuse the bounded core-only protocol deployment with a separate rollback report."""
import deploy_responses

deploy_responses.KIND = 'model-controls'
deploy_responses.REQUIRED_SETTINGS = ('api_style', 'reasoning_effort', 'model_stream_enabled')

if __name__ == '__main__':
    deploy_responses.main()
