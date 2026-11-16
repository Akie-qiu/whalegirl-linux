"""Loopback-only, authenticated GPT-SoVITS adapter for a restricted SSH tunnel.

Only speech text, a configured reference ID and two owner-installed voice IDs are
accepted. No arbitrary model paths, files or shell endpoints. Request IDs survive restarts; uncertain work is
never silently synthesized twice. Speech text is not stored or logged here.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import io
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid
import wave

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pc_voice_common import read_control
from pc_voice_engine import load_engine, tempo_wav, voice_catalog
from pc_voice_selection import selection_status, request_switch


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(pattern='^[0-9a-f]{32}$')
    text: str = Field(min_length=1, max_length=600)
    preset: str = Field(pattern='^[a-zA-Z0-9_-]{1,40}$')
    language: str = Field(default='auto', pattern='^(auto|zh|yue|en|ja|ko)$')
    speed_factor: float = Field(default=1, ge=.7, le=1.4, allow_inf_nan=False)
    fragment_interval: float = Field(default=.3, ge=.12, le=.5, allow_inf_nan=False)


class HandshakeAck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    nonce: str = Field(pattern='^[0-9a-f]{32}$')
    instance_id: str = Field(pattern='^[0-9a-f]{32}$')
    accepted: bool


class VoiceSwitchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    voice_id: str = Field(pattern='^(cyrene|yachiyo|xiaofei)$')
    request_id: str = Field(pattern='^[0-9a-f]{32}$')


def make_app(config_path, *, transport=None):
    path = Path(config_path)
    settings = json.loads(path.read_text(encoding='utf-8'))
    assert len(settings['token']) >= 40
    assert settings['upstream'] == 'http://127.0.0.1:9881'
    engine = load_engine(path.parent)
    active_voice = engine.get('preset_id', 'cyrene')
    directory = path.parent / 'bridge-cache'
    directory.mkdir(exist_ok=True)
    db = sqlite3.connect(directory / 'requests.sqlite3', check_same_thread=False)
    db.execute('CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,digest TEXT NOT NULL,'
               'state TEXT NOT NULL,created REAL NOT NULL,seconds REAL NOT NULL DEFAULT 0)')
    if 'engine' not in {row[1] for row in db.execute('PRAGMA table_info(requests)')}:
        db.execute("ALTER TABLE requests ADD COLUMN engine TEXT NOT NULL DEFAULT 'gpt-sovits'")
    if 'language' not in {row[1] for row in db.execute('PRAGMA table_info(requests)')}:
        db.execute("ALTER TABLE requests ADD COLUMN language TEXT NOT NULL DEFAULT 'zh'")
    if 'voice' not in {row[1] for row in db.execute('PRAGMA table_info(requests)')}:
        db.execute("ALTER TABLE requests ADD COLUMN voice TEXT NOT NULL DEFAULT 'cyrene'")
    db.execute("UPDATE requests SET state='unknown' WHERE state='running'")
    db.commit()
    lock = asyncio.Lock()
    instance_id = uuid.uuid4().hex
    challenges = {}
    cloud = {'ack_at': 0.0, 'accepted': False}
    client = httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                              timeout=httpx.Timeout(settings.get('timeout', 180), connect=3))

    @asynccontextmanager
    async def lifespan(app):
        yield
        await client.aclose()
        db.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    async def authorize(authorization: str = Header(default='')):
        if not hmac.compare_digest(authorization, 'Bearer ' + settings['token']):
            raise HTTPException(401, 'Unauthorized')

    @app.middleware('http')
    async def small_body(request: Request, call_next):
        if request.method == 'POST':
            size = request.headers.get('content-length', '')
            if not size.isdigit() or not 0 < int(size) <= 8192:
                return JSONResponse({'detail': 'Request size rejected'}, 413)
            # Check the actual body too; do not trust a forged Content-Length.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 8192:
                    return JSONResponse({'detail': 'Request size rejected'}, 413)
            request._body = bytes(body)
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({'detail': 'Invalid speech parameters'}, 422)

    @app.get('/health', dependencies=[Depends(authorize)])
    async def health(x_voice_handshake: str = Header(default='')):
        switch = selection_status(path.parent)
        stopping = (not read_control(path.parent)['desired']
                    or switch.get('phase') in {'queued', 'stopping', 'rolling_back'})
        try:
            if stopping:
                ready = False
            elif lock.locked():
                ready = True
            elif engine['backend'] == 'aniflive':
                response = await client.get(settings['upstream'] + '/health', timeout=2)
                value = response.json() if response.status_code == 200 else {}
                ready = (value.get('ready') is True and value.get('backend') == 'TensorRT-11'
                    and value.get('engine_count') == 9 and value.get('engine_fingerprint') == engine['fingerprint'])
            elif engine['backend'] == 'gsv-lite':
                response = await client.get(settings['upstream'] + '/health',
                    headers={'Authorization': 'Bearer ' + settings['token']}, timeout=2)
                value = response.json() if response.status_code == 200 else {}
                ready = (value.get('ready') is True and value.get('backend') == 'gsv-lite'
                    and value.get('engine_fingerprint') == engine['fingerprint']
                    and value.get('active_voice') == engine['voice_id'])
            else:
                ready = (await client.get(settings['upstream'] + '/docs', timeout=2)).status_code == 200
        except (httpx.HTTPError, ValueError, AttributeError):
            ready = False
        if x_voice_handshake:
            if not re.fullmatch('[0-9a-f]{32}', x_voice_handshake):
                raise HTTPException(422, 'Invalid handshake')
            for key in list(challenges):
                if time.monotonic() - challenges[key][0] > 15:
                    challenges.pop(key)
            if len(challenges) >= 8:
                challenges.pop(next(iter(challenges)))
            challenges[x_voice_handshake] = (time.monotonic(), bool(ready and not stopping))
        supported_languages = (['auto', 'zh', 'ja'] if engine['backend'] == 'gsv-lite'
                               else ['auto', 'zh', 'yue', 'en', 'ja', 'ko'])
        return {'ready': bool(ready), 'busy': lock.locked(), 'stopping': stopping, 'backend': engine['backend'],
                'engine_fingerprint': engine['fingerprint'],
                'active_voice': active_voice, 'voice_switch': switch,
                'voices': [{'id': row['id'], 'name': row['name'], 'backend': row['engine']['backend']}
                           for row in voice_catalog(path.parent)],
                'handshake_nonce': x_voice_handshake, 'instance_id': instance_id,
                'cloud_ack_at': cloud['ack_at'], 'cloud_accepted': cloud['accepted'],
                'speech_controls': ['speed_factor', 'fragment_interval'],
                'languages': supported_languages,
                'speech_control_modes': {'speed_factor': 'ffmpeg-atempo' if engine['backend'] == 'aniflive' else 'native',
                                         'fragment_interval': 'model-pause_length' if engine['backend'] == 'aniflive' else 'native'},
                'presets': [active_voice], 'cache_bytes': sum(p.stat().st_size for p in directory.glob('*.wav'))}

    @app.post('/v1/voices/switch', dependencies=[Depends(authorize)])
    async def switch_voice(body: VoiceSwitchRequest):
        try:
            return request_switch(path.parent, body.voice_id, body.request_id)
        except (OSError, ValueError):
            raise HTTPException(409, '声线未安装或已有切换正在进行，请核对状态') from None

    @app.post('/v1/handshake/ack', dependencies=[Depends(authorize)])
    async def acknowledge(body: HandshakeAck):
        challenge = challenges.pop(body.nonce, None)
        if (body.instance_id != instance_id or not challenge or time.monotonic() - challenge[0] > 15
                or body.accepted and (not challenge[1] or not read_control(path.parent)['desired'])):
            raise HTTPException(409, 'Handshake changed or expired')
        cloud.update(ack_at=time.time(), accepted=body.accepted)
        return {'acknowledged': True, 'accepted': body.accepted, 'instance_id': instance_id}

    @app.post('/v1/tts', dependencies=[Depends(authorize)])
    async def synthesize(body: SpeechRequest):
        if (not read_control(path.parent)['desired']
                or selection_status(path.parent).get('phase') in {'queued', 'stopping', 'rolling_back'}):
            raise HTTPException(503, 'Owner stopped local voice; no synthesis submitted')
        preset_valid = (body.preset == 'xiaofei' if engine['backend'] == 'gsv-lite'
                        else body.preset in settings['presets'])
        if (not preset_valid or body.preset != active_voice
                or len(body.text) > settings.get('max_text', 600)):
            raise HTTPException(422, 'Unknown preset or excessive text')
        if engine['backend'] == 'gsv-lite' and body.language not in {'auto', 'zh', 'ja'}:
            raise HTTPException(422, 'Unsupported language for the active local voice')
        parameters = [body.text, body.preset]
        if body.speed_factor != 1 or body.fragment_interval != .3:
            parameters.append({'speed_factor': body.speed_factor, 'fragment_interval': body.fragment_interval})
        if body.language != 'auto':
            parameters.append({'language': body.language})
        digest = hashlib.sha256(json.dumps(parameters, ensure_ascii=False).encode()).hexdigest()
        cached = db.execute('SELECT digest,state,engine,language,voice FROM requests WHERE id=?', (body.request_id,)).fetchone()
        output = directory / (body.request_id + '.wav')
        if cached:
            if cached[0] != digest:
                raise HTTPException(409, 'Request ID conflicts with earlier parameters')
            if cached[1] == 'done' and output.is_file():
                return Response(output.read_bytes(), media_type='audio/wav',
                                headers={'X-Voice-Cache': 'hit', 'X-Voice-Backend': cached[2],
                                         'X-Voice-Language': cached[3], 'X-Voice-ID': cached[4]})
            raise HTTPException(409, 'Request already running or uncertain; no regeneration')
        if lock.locked():
            raise HTTPException(429, 'Local voice is busy; no request accepted')
        if (sum(p.stat().st_size for p in directory.glob('*.wav')) + settings['max_bytes'] > 64 * 1024**2
                or db.execute('SELECT count(*) FROM requests').fetchone()[0] >= 2048):
            raise HTTPException(507, 'Local voice cache needs review; no request accepted')
        async with lock:
            # No await between the lock test and acquisition; one event loop owns
            # the ledger. Persist acceptance before starting inference.
            db.execute('INSERT INTO requests(id,digest,state,created,engine,language,voice) VALUES(?,?,?,?,?,?,?)',
                       (body.request_id, digest, 'running', time.time(), engine['backend'], body.language, active_voice))
            db.commit()
            started = time.monotonic()
            preset = {} if engine['backend'] == 'gsv-lite' else settings['presets'][body.preset]
            payload = {'text': body.text, 'text_lang': body.language, **preset, 'media_type': 'wav',
                       'streaming_mode': False, 'batch_size': 1, 'parallel_infer': False,
                       'text_split_method': 'cut5', 'speed_factor': body.speed_factor,
                       'fragment_interval': body.fragment_interval, 'seed': 42}
            endpoint = '/tts'
            headers = None
            if engine['backend'] == 'aniflive':
                endpoint = '/v1/audio/speech'
                payload = {'model': engine['model_id'], 'voice': engine['voice_id'], 'input': body.text,
                           'text_lang': body.language, 'stream': False, 'response_format': 'wav', 'speed': 1.0,
                           'pause_length': body.fragment_interval * body.speed_factor, 'seed': 42}
            elif engine['backend'] == 'gsv-lite':
                endpoint = '/v1/tts'
                headers = {'Authorization': 'Bearer ' + settings['token']}
                payload = {'request_id': body.request_id, 'text': body.text, 'preset': body.preset,
                           'language': body.language, 'speed_factor': body.speed_factor,
                           'fragment_interval': body.fragment_interval}
            try:
                async with client.stream('POST', settings['upstream'] + endpoint, json=payload,
                                         headers=headers) as result:
                    if result.status_code != 200:
                        raise ValueError('Synthesis failed')
                    if engine['backend'] == 'aniflive' and (
                            result.headers.get('X-TensorRT-Backend') != 'TensorRT-11'
                            or result.headers.get('X-PyTorch-Fallback') != 'false'
                            or result.headers.get('X-TensorRT-Engine-Count') != '9'):
                        raise ValueError('Unverified AnifLive runtime response')
                    if engine['backend'] == 'gsv-lite' and (
                            result.headers.get('X-GSV-TTS-Backend') != 'GSV-TTS-Lite-CUDA'
                            or result.headers.get('X-GSV-TTS-Fingerprint') != engine['fingerprint']
                            or result.headers.get('X-Voice-ID') != engine['voice_id']):
                        raise ValueError('Unverified GSV-TTS-Lite runtime response')
                    data = bytearray()
                    async for chunk in result.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > settings['max_bytes']:
                            raise ValueError('Audio too large')
                if engine['backend'] == 'aniflive':
                    data = await tempo_wav(data, body.speed_factor, settings['max_bytes'])
                with wave.open(io.BytesIO(data)) as wav:
                    duration = wav.getnframes() / wav.getframerate()
                    if not .1 <= duration <= 120 or wav.getsampwidth() != 2:
                        raise ValueError('Invalid generated audio')
                output.write_bytes(data)
                db.execute("UPDATE requests SET state='done',seconds=? WHERE id=?",
                           (time.monotonic() - started, body.request_id))
                db.commit()
                return Response(bytes(data), media_type='audio/wav',
                                headers={'X-Voice-Cache': 'miss', 'X-Voice-Seconds': str(round(duration, 3)),
                                         'X-Voice-Backend': engine['backend'], 'X-Voice-Language': body.language,
                                         'X-Voice-ID': active_voice})
            except (httpx.HTTPError, ValueError, wave.Error, OSError, asyncio.TimeoutError):
                db.execute("UPDATE requests SET state='unknown' WHERE id=?", (body.request_id,))
                db.commit()
                raise HTTPException(502, 'Local synthesis failed or interrupted; no automatic regeneration') from None

    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(make_app(args.config), host='127.0.0.1', port=9882, access_log=False, log_level='warning')
