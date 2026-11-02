"""One model planning preview with live references; never enqueue or send."""
import asyncio
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

from cryptography.fernet import Fernet
from loguru import logger

sys.path.insert(0, '/opt/whalegirl-linux/current')

from whalegirl_linux.image_references import ReferenceLibrary
from whalegirl_linux.image_tools import ImageTools
from whalegirl_linux.images import ImageService
from whalegirl_linux.moments_world import MomentsWorld
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.upstream.ai_engine import AIEngine


async def main():
    logger.remove()
    data = Path('/var/lib/whalegirl-linux/core')
    target = Path(sys.argv[1])
    if target.exists():
        raise RuntimeError('Do not repeat this planning preview')
    # Read credentials/settings without Config's creation/migration side effects.
    config = object.__new__(Config)
    config.directory = data
    config.settings = Settings.model_validate(json.loads((data / 'settings.json').read_text()))
    config.fernet = Fernet((data / 'encryption.key').read_text().strip().encode())
    s = config.settings
    world = object.__new__(MomentsWorld)
    world.config = config
    images = object.__new__(ImageService)
    images.config = config
    images.references = ReferenceLibrary(config)
    images.runtime = SimpleNamespace(moments=SimpleNamespace(world=world))
    tools = object.__new__(ImageTools)
    tools.images = images
    tools.config = config
    system = s.system_prompt_override + tools.static_prompt(include_catalog=False) + (
        '\n只输出JSON：{"should_reply":true,"actions":[],"image_request":null}。'
        '需要工具时按图片工具格式填写image_request，动作文字自然简短。'
        '当前生图额度充足，没有进行中的任务，用户是在直接要求你作画。')
    usage = []
    engine = AIEngine(api_url=s.api_url, api_key=config.api_key(), model=s.model, api_style=s.api_style,
        reasoning_effort=s.reasoning_effort, stream_enabled=s.model_stream_enabled,
        max_tokens=850, temperature=.55, timeout=30, max_retries=1)
    engine.usage_callback = usage.append
    started = time.monotonic()
    try:
        raw = await engine.chat([{'role': 'user', 'content': '画一张图，看看你们几个AI之间的生活'}],
            system_prompt=system, response_format={'type': 'json_object'}, operation='image')
        value = json.loads(raw)
        request = value.get('image_request') or {}
        actors = request.get('cast_ids') or []
        plan = images.bind_cast({}, actors)
        refs = plan['inputs']
        parts = images.references.upload_parts(refs)
        result = {'tool': request.get('tool'), 'cast_ids': actors, 'reference_count': len(refs),
            'partner_reference_count': sum(r['source'] == 'story_character' for r in refs),
            'reference_files_ready': len(parts) == len(refs),
            'all_four_selected': set(actors) == {'whalegirl', 'gpt', 'claude', 'gemini'},
            'seconds': round(time.monotonic() - started, 3), 'model_calls': 1,
            'image_requests_submitted': 0, 'native_messages_sent': 0,
            'usage': [{k: u.get(k) for k in ('input_tokens', 'output_tokens', 'total_tokens', 'cached_tokens')} for u in usage]}
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        target.chmod(0o600)
        print(json.dumps(result, ensure_ascii=False))
        if request.get('tool') != 'generate_image' or not result['all_four_selected']:
            raise RuntimeError('The live model did not select the expected cast')
    finally:
        await engine.close()


if __name__ == '__main__':
    asyncio.run(main())
