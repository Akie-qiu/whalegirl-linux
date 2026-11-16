"""Read-only model bridge for the isolated personality evaluation.

Installed into a private /tmp source-only sandbox. The real key is decrypted only
in this short-lived process, never returned, written, or logged. No Runtime,
production database, gateway, or platform client is opened.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time

CORE = Path('/var/lib/whalegirl-linux/core')
PUBLIC_SETTINGS = ('system_prompt_override', 'model', 'max_tokens', 'api_style',
    'model_temperature', 'reasoning_effort', 'model_stream_enabled',
    'natural_group_replies', 'context_messages', 'colloquialism', 'teasing',
    'persona_density', 'followup_questions', 'reply_length', 'girlish_tone',
    'chat_short_replies_enabled', 'chat_short_reply_sentences',
    'chat_short_reply_chars', 'reply_warmth', 'banter_level', 'play_along',
    'callback_frequency', 'assertiveness', 'affection_enabled',
    'affection_initial_score', 'affection_private_style', 'private_unreserved_enabled',
    'fishtalk_enabled', 'fishtalk_auto_learn', 'fishtalk_reference_level',
    'fishtalk_check_mode', 'fishtalk_sample_limit', 'fishtalk_sample_days',
    'memory_enabled', 'memory_auto_capture', 'memory_recall_limit',
    'history_recall_limit', 'context_char_budget', 'background_digest_enabled',
    'growth_enabled', 'growth_memory_enabled', 'growth_memory_max_chars',
    'growth_bilibili_expression_shared', 'growth_tieba_expression_shared')


def protected():
    return {name: hashlib.sha256((CORE / name).read_bytes()).hexdigest() for name in
        ('settings.json', 'persona-cards.json', 'model-profiles.json',
         'model-api-keys.enc', 'bilibili-session.enc') if (CORE / name).exists()}


def inputs(with_key=False):
    settings = json.loads((CORE / 'settings.json').read_text())
    profiles = json.loads((CORE / 'model-profiles.json').read_text())
    active = settings.get('active_model_profile') or profiles.get('active') or 'custom'
    profile = profiles['profiles'][active]
    key = None
    if with_key:
        from cryptography.fernet import Fernet
        keys = json.loads(Fernet((CORE / 'encryption.key').read_bytes().strip()).decrypt(
            (CORE / 'model-api-keys.enc').read_bytes()))
        key = keys.get(active)
        if not key:
            raise RuntimeError('selected_model_has_no_key')
    return settings, profile, key


def memory():
    values = {line.split(':')[0]: int(line.split()[1]) for line in
        Path('/proc/meminfo').read_text().splitlines() if ':' in line}
    status = Path('/proc/self/status').read_text().splitlines()
    return {'available_mib': round(values['MemAvailable'] / 1024, 2),
        'rss_mib': round(next(int(line.split()[1]) for line in status if line.startswith('VmRSS:')) / 1024, 3),
        'peak_rss_mib': round(next(int(line.split()[1]) for line in status if line.startswith('VmHWM:')) / 1024, 3)}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('metadata', 'request'), required=True)
    parser.add_argument('--code', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.code))
    from loguru import logger
    logger.remove()
    before = protected()
    started = time.monotonic()
    engine = None
    result = {'ok': False, 'actual_requests': 0}
    try:
        if memory()['available_mib'] < 128:
            raise RuntimeError('host_memory_guard')
        settings, profile, key = inputs(args.mode == 'request')
        if args.mode == 'metadata':
            public = {name: settings[name] for name in PUBLIC_SETTINGS if name in settings}
            public.update({name: profile[name] for name in PUBLIC_SETTINGS if name in profile})
            public['api_url'] = 'https://evaluation.invalid/chat/completions'
            result.update(ok=True, settings=public, persona_sha256=hashlib.sha256(
                public.get('system_prompt_override', '').encode()).hexdigest())
        else:
            payload = json.loads(sys.stdin.read(4000000))
            if not isinstance(payload, dict) or not isinstance(payload.get('messages'), list):
                raise RuntimeError('evaluation_payload_rejected')
            from whalegirl_linux.upstream.ai_engine import AIEngine
            import httpx
            engine = AIEngine(api_url=profile.get('api_url') or settings['api_url'],
                api_key=key, model=profile.get('model') or settings['model'],
                max_tokens=profile.get('max_tokens', settings.get('max_tokens', 500)),
                temperature=profile.get('model_temperature', settings.get('model_temperature')) or 1.2,
                api_style=profile.get('api_style', settings.get('api_style', 'auto')),
                reasoning_effort=profile.get('reasoning_effort', settings.get('reasoning_effort', 'default')),
                stream_enabled=profile.get('model_stream_enabled', settings.get('model_stream_enabled', False)),
                timeout=45, max_retries=0)
            events = []
            engine.usage_callback = events.append
            original = httpx.AsyncClient.send
            attempts = []
            async def send(client, request, *options, **kw):
                if attempts or request.method != 'POST' or not any(part in request.url.path for part in
                    ('chat/completions', 'responses', 'messages')):
                    raise RuntimeError('single_model_request_only')
                attempts.append({'started': time.time(), 'bytes': len(request.content)})
                response = await original(client, request, *options, **kw)
                attempts[-1]['http_status'] = response.status_code
                return response
            httpx.AsyncClient.send = send
            answer = await engine.chat(payload['messages'], **{name: payload[name] for name in
                ('system_prompt', 'temperature', 'max_tokens', 'response_format', 'operation') if name in payload})
            result.update(ok=bool(answer) and not engine.last_error, answer=answer,
                actual_requests=len(attempts), attempts=attempts, usage=events,
                error_code='model_request_failed' if engine.last_error else '',
                first_token_ms=engine.last_first_token_ms,
                response_meta={name: value for name, value in engine.last_response_meta.items()
                               if name in ('finish_reason', 'attempts', 'request_fingerprint')})
    except Exception as error:
        result.update(error_type=type(error).__name__, error_code=str(error)
            if str(error) in ('host_memory_guard', 'selected_model_has_no_key', 'evaluation_payload_rejected')
            else 'isolated_bridge_incomplete')
    finally:
        if engine:
            await engine.close()
        result.update(seconds=round(time.monotonic() - started, 4), resources=memory(),
            production_config_unchanged=protected() == before, production_db_opened=False,
            native_sends=0)
    print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
