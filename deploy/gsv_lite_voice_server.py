"""Loopback-only GSV-TTS-Lite adapter for the owner's Xiaofei V2 model."""
import argparse
import asyncio
import hmac
import io
import json
import logging
import math
from pathlib import Path
import re
import sys
import time

import numpy as np
import soundfile as sf
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

# The supervisor starts this file with -I; explicitly allow only its own
# trusted sibling modules, without relying on cwd or PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pc_voice_common import read_control, write_json
from pc_voice_engine import GSV_LITE_FILES, gsv_lite_fingerprint, load_engine


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(pattern='^[0-9a-f]{32}$')
    text: str = Field(min_length=1, max_length=600)
    preset: str = Field(pattern='^xiaofei$')
    language: str = Field(default='auto', pattern='^(auto|zh|ja)$')
    speed_factor: float = Field(default=1, ge=.7, le=1.4, allow_inf_nan=False)
    fragment_interval: float = Field(default=.3, ge=.12, le=.5, allow_inf_nan=False)


def load_tts(runtime, package_name):
    root = Path(runtime).resolve()
    selected = load_engine(root)
    if selected['backend'] != 'gsv-lite' or selected['preset_id'] != 'xiaofei':
        raise ValueError('Xiaofei is not the selected local voice')
    if selected['command'][selected['command'].index('--package') + 1] != package_name:
        raise ValueError('Selected GSV-TTS-Lite package changed')

    config = json.loads((root / 'bridge.json').read_text(encoding='utf-8'))
    package = (root.parent / 'gsv-lite' / 'models' / package_name).resolve()
    model_root = (root.parent / 'gsv-lite' / 'models').resolve()
    if not package.is_relative_to(model_root):
        raise ValueError('Model package is outside the local GSV-TTS-Lite directory')
    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
    if gsv_lite_fingerprint(package) != selected['fingerprint']:
        raise ValueError('GSV-TTS-Lite package failed integrity validation')

    assets = root.parent / 'gsv-lite' / 'pretrained'
    required = (assets / 'chinese-hubert-base', assets / 'g2p' / 'zh' / 'opencpop-strict.txt',
                assets / 'sv' / 'pretrained_eres2netv2w24s4ep4.ckpt')
    if not all(path.exists() for path in required):
        raise ValueError('Required local GSV-TTS-Lite phoneme or speaker assets are missing')

    # The upstream library logs a prefix of the spoken text at INFO level.
    # Keep chat text out of worker logs while retaining warnings and errors.
    logging.disable(logging.INFO)
    from gsv_tts.TTS import TTS
    import gsv_tts.Config as gsv_config
    import gsv_tts.GPT_SoVITS.GPT.t2s_model as gsv_t2s_model
    from torch.nn.attention import SDPBackend
    gsv_config.SDPBACKEND = SDPBackend.EFFICIENT_ATTENTION
    gsv_t2s_model.SDPBACKEND = SDPBackend.EFFICIENT_ATTENTION

    tts = TTS(models_dir=str(assets), device='cuda', dtype='float16', use_flash_attn=False,
              use_bert=False, gpt_cache=[(1, 512), (1, 768)], sovits_cache=[50, 55])
    gpt_model, sovits_model = str(package / 'gpt'), str(package / 'sovits')
    reference = str(package / 'reference.wav')
    tts.load_gpt_model(gpt_model)
    tts.load_sovits_model(sovits_model)
    tts.init_language_module('zh', 'ja')
    tts.cache_prompt_audio(reference, manifest['prompt_text'], manifest['prompt_language'])
    tts.cache_spk_audio(reference, sovits_model=sovits_model)
    return {'tts': tts, 'token': config['token'], 'package': package,
            'fingerprint': selected['fingerprint'], 'voice_id': 'xiaofei',
            'prompt_text': manifest['prompt_text'], 'prompt_language': manifest['prompt_language'],
            'gpt_model': gpt_model, 'sovits_model': sovits_model,
            'max_bytes': int(config['max_bytes']), 'runtime': root}


def make_app(state):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    inference_lock = asyncio.Lock()
    state['busy'] = False

    async def authorize(authorization: str = Header(default='')):
        if not hmac.compare_digest(authorization, 'Bearer ' + state['token']):
            raise HTTPException(401, 'Unauthorized')

    @app.middleware('http')
    async def bounded_body(request: Request, call_next):
        if request.method == 'POST':
            content_length = request.headers.get('content-length')
            if content_length and (not content_length.isdigit() or int(content_length) > 8192):
                return JSONResponse({'detail': 'Request size rejected'}, 413)
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({'detail': 'Invalid speech parameters'}, 422)

    def current_selection():
        try:
            value = json.loads((state['runtime'] / 'engine.json').read_text(encoding='utf-8'))
            return (read_control(state['runtime'])['desired'] and value.get('backend') == 'gsv-lite'
                    and value.get('voice_id') == state['voice_id']
                    and value.get('fingerprint') == state['fingerprint'])
        except (OSError, ValueError, KeyError, TypeError):
            return False

    @app.get('/health', dependencies=[Depends(authorize)])
    async def health():
        return {'ready': True, 'busy': state['busy'], 'backend': 'gsv-lite',
                'engine': 'GSV-TTS-Lite-CUDA', 'engine_fingerprint': state['fingerprint'],
                'active_voice': state['voice_id'], 'languages': ['auto', 'zh', 'ja']}

    @app.post('/v1/tts', dependencies=[Depends(authorize)])
    async def synthesize(body: SpeechRequest):
        if not current_selection():
            raise HTTPException(503, 'Xiaofei is not selected or voice service is stopping')
        if inference_lock.locked():
            raise HTTPException(429, 'Local voice is busy; no request accepted')

        async with inference_lock:
            state['busy'] = True
            started = time.monotonic()
            try:
                def infer():
                    clips = state['tts'].infer_batched(
                        spk_audio_paths=str(state['package'] / 'reference.wav'),
                        prompt_audio_paths=str(state['package'] / 'reference.wav'),
                        prompt_audio_texts=state['prompt_text'],
                        texts=[body.text], text_languages=body.language,
                        prompt_languages=state['prompt_language'], is_cut_text=True,
                        cut_minlen=10, cut_mute=body.fragment_interval,
                        speed=body.speed_factor, gpt_model=state['gpt_model'],
                        sovits_model=state['sovits_model'])
                    return clips[0]

                clip = await asyncio.to_thread(infer)
                audio = np.asarray(clip.audio_data, dtype=np.float32)
                if (audio.ndim != 1 or not audio.size or not np.isfinite(audio).all()
                        or not math.isfinite(float(clip.samplerate)) or clip.samplerate != 32000):
                    raise ValueError('Invalid GSV-TTS-Lite output')
                output = io.BytesIO()
                sf.write(output, audio, int(clip.samplerate), format='WAV', subtype='PCM_16')
                data = output.getvalue()
                if not 44 < len(data) <= state['max_bytes']:
                    raise ValueError('GSV-TTS-Lite output exceeds the configured size limit')
                return Response(data, media_type='audio/wav', headers={
                    'X-GSV-TTS-Backend': 'GSV-TTS-Lite-CUDA',
                    'X-GSV-TTS-Fingerprint': state['fingerprint'],
                    'X-GSV-TTS-Seconds': str(round(time.monotonic() - started, 3)),
                    'X-Voice-ID': state['voice_id'],
                })
            except HTTPException:
                raise
            except Exception as exc:
                logging.getLogger(__name__).warning('Xiaofei inference failed: %s', type(exc).__name__)
                raise HTTPException(502, 'Local voice synthesis failed; no automatic retry') from None
            finally:
                state['busy'] = False

    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--package', choices=['xiaofei-v2'], required=True)
    parser.add_argument('--port', choices=['9881'], default='9881')
    args = parser.parse_args()
    startup = Path(args.runtime) / 'model-startup.json'
    try:
        write_json(startup, {'phase': 'loading', 'backend': 'gsv-lite', 'at': time.time()})
        state = load_tts(args.runtime, args.package)
        write_json(startup, {'phase': 'model_loaded', 'backend': 'gsv-lite', 'at': time.time()})
        import uvicorn
        uvicorn.run(make_app(state), host='127.0.0.1', port=int(args.port), access_log=False, log_level='warning')
    except Exception as exc:
        # Store only exception metadata, never exception text, chat or tokens.
        module = getattr(exc, 'name', '') or ''
        if not re.fullmatch(r'[a-zA-Z0-9_.]{1,100}', module):
            module = ''
        write_json(startup, {'phase': 'failed', 'backend': 'gsv-lite', 'at': time.time(),
            'error_type': type(exc).__name__, 'module': module})
        raise
