"""Isolated audio console fixture; synthetic media, no upstream or WeChat writes."""
import argparse
import asyncio
import base64
from contextlib import asynccontextmanager
import io
import os
from pathlib import Path
import sys
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from whalegirl_linux.app import create_app
from whalegirl_linux.settings import Settings
import uvicorn


class PreviewGateway:
    login = {}

    async def status(self):
        return {'status': 'logged_in', 'loggedInUser': 'audio-preview-account'}

    async def chats(self):
        return [{'id': 'audio-preview@chatroom', 'name': '音频离线验收群', 'isGroup': True}]

    async def messages(self, *args, **kwargs):
        return []

    async def close(self):
        pass


class SyntheticAudioProvider:
    async def generate(self, capture, kind, arguments):
        output = io.BytesIO()
        with wave.open(output, 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(48000)
            audio.writeframes(b'\x00\x00' * 24000)
        return {'base64': base64.b64encode(output.getvalue()).decode(), 'usage': {'total_tokens': 0}, 'generation_seconds': .01}

    async def download(self, result, path, cap):
        path.write_bytes(base64.b64decode(result['base64']))

    async def close(self):
        pass


def forbidden_engine(**kwargs):
    raise RuntimeError('Offline audio fixture never calls a model')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18118)
    args = parser.parse_args()
    parent = ROOT / 'artifacts/audio-implementation-20261108'
    parent.mkdir(parents=True, exist_ok=True)
    data = Path(tempfile.mkdtemp(prefix='ui-fixture-', dir=parent))
    (data / 'admin.token').write_text('audio-ui-local-only', encoding='utf-8')
    os.environ['WHALEGIRL_PUBLIC_ORIGINS'] = f'http://127.0.0.1:{args.port}'
    app = create_app(data, gateway=PreviewGateway(), engine_factory=forbidden_engine, run_background=False)
    runtime = app.state.runtime
    app.state.config.save(Settings(enabled=True, account_id='audio-preview-account', allowed_chat_ids=['audio-preview@chatroom'],
                                   api_style='openai', model_stream_enabled=False), 'synthetic-chat-key')
    audio = runtime.audio.settings.snapshot()
    for key, hostname in [('speech', 'speech.example'), ('music', 'music.example'), ('music-pro', 'pro.example')]:
        audio['settings']['providers'][key]['api_url'] = 'https://' + hostname
    runtime.audio.settings.save(audio['revision'], audio['settings'], {key: 'synthetic-' + key for key in audio['settings']['providers']})
    lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def offline_lifespan(app):
        async with lifespan(app):
            await runtime.audio.provider.close()
            runtime.audio.provider = SyntheticAudioProvider()
            worker = asyncio.create_task(runtime.audio.loop(generate=True))
            try:
                yield
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
    app.router.lifespan_context = offline_lifespan
    print('Offline fixture directory: ' + str(data), flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning', access_log=False)


if __name__ == '__main__':
    main()
