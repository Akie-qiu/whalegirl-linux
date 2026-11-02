"""One real model preview in an isolated ledger; no WeChat or image submission."""
import asyncio
import json
from pathlib import Path
import sqlite3
import sys
import time
from types import SimpleNamespace

from cryptography.fernet import Fernet
from loguru import logger

from whalegirl_linux.image_references import ReferenceLibrary
from whalegirl_linux.moments import MomentsBook, MomentsService
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store
from whalegirl_linux.upstream.ai_engine import AIEngine


async def main():
    logger.remove()
    data = Path('/var/lib/whalegirl-linux/core')
    target = Path(sys.argv[1])
    if target.exists():
        raise RuntimeError('Preview report already exists')
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = object.__new__(Config)
    config.directory = data
    config.generation = 0
    config.settings = Settings.model_validate(json.loads((data / 'settings.json').read_text()))
    config.fernet = Fernet((data / 'encryption.key').read_text().strip().encode())
    if not config.settings.system_prompt_override.strip():
        raise RuntimeError('No explicit active persona; do not substitute an invented one in this preview')
    # Preview overrides are in memory only and never saved to live settings.
    config.settings = config.settings.model_copy(update={'moments_enabled': True,
        'moments_publish_enabled': True, 'moments_publish_per_day': max(1, config.settings.moments_publish_per_day)})
    usage, jobs, staged, model_calls = [], [], [], []
    class PreviewEngine(AIEngine):
        async def chat(self, *args, **kwargs):
            model_calls.append(1)
            return await super().chat(*args, **kwargs)
    live = sqlite3.connect('file:' + str(data / 'state.sqlite3') + '?mode=ro', uri=True)
    live.row_factory = sqlite3.Row
    try:
        old = object.__new__(MomentsBook)
        old.store = SimpleNamespace(db=live)
        history = old.publications(config.settings.account_id, config.settings.moments_variety_days)
    finally:
        live.close()
    store = Store(target.parent / 'preview-state.sqlite3')
    runtime = SimpleNamespace(store=store, config=config, engine_factory=PreviewEngine,
        styles=SimpleNamespace(get_active=lambda: SimpleNamespace(system_prompt='')),
        token_ledger=SimpleNamespace(record=lambda account, event: usage.append(event)),
        images=SimpleNamespace(candidates=lambda *a, **k: [], generation_count=lambda: 0,
            references=ReferenceLibrary(config), enqueue=lambda **kwargs: jobs.append(kwargs),
            stage=lambda asset, **kwargs: staged.append(asset)),
        gateway=SimpleNamespace(ui_lock=asyncio.Lock(), inflight_sends=set()),
        gateway_state={'status': 'logged_in'}, account_id=config.settings.account_id,
        paused_reason='', processing='', safety=SimpleNamespace(held=lambda account: ''))
    service = MomentsService(runtime)
    service.publication_history = lambda identity: history
    started = time.monotonic()
    try:
        row = await service.generate('publish')
        payload = json.loads(row['payload'])
        result = {'state': row['state'], 'text': row['text'], 'error': row['error'],
            'story': {key: payload.get(key, '') for key in ('story_seed', 'story_hook', 'story_progress')},
            'image_prompt': payload.get('image_prompt', ''), 'visual_concept': payload.get('visual_concept', ''),
            'model_seconds': round(time.monotonic() - started, 3), 'model_calls': len(model_calls),
            'native_messages_sent': 0, 'image_requests_submitted': 0,
            'preview_image_plans': len(jobs), 'isolated_ledger': str(target.parent / 'preview-state.sqlite3'),
            'usage': [{key: event.get(key) for key in ('input_tokens', 'output_tokens', 'total_tokens', 'cached_tokens')}
                      for event in usage]}
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        target.chmod(0o600)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        store.close()


if __name__ == '__main__':
    asyncio.run(main())
