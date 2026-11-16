"""Owner-selected local engine; never accepts paths or engine changes over HTTP."""
import argparse
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import wave

LEGACY_PYTHON = Path('D:/anconda/envs/GPTSoVits/python.exe')
LEGACY_ROOT = Path('D:/yachiyo/voice')
GSV_LITE_VERSION = '0.4.7'
GSV_LITE_FILES = ('gpt/config.json', 'gpt/model.safetensors',
                  'sovits/hps.json', 'sovits/model.safetensors', 'reference.wav')


def gsv_lite_fingerprint(package):
    package = Path(package).resolve()
    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('format') != 1 or manifest.get('voice_id') != 'xiaofei'
            or manifest.get('model_family') != 'gpt-sovits-v2'
            or manifest.get('runtime_version') != GSV_LITE_VERSION
            or manifest.get('files') != list(GSV_LITE_FILES)):
        raise ValueError('Invalid GSV-TTS-Lite model manifest')
    checksums = manifest.get('checksums')
    if not isinstance(checksums, dict) or set(checksums) != set(GSV_LITE_FILES):
        raise ValueError('Invalid GSV-TTS-Lite model checksums')
    for relative in GSV_LITE_FILES:
        path = (package / relative).resolve()
        if not path.is_relative_to(package) or not path.is_file():
            raise ValueError('GSV-TTS-Lite model file is missing or outside its package')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        if checksums.get(relative) != digest.hexdigest():
            raise ValueError('GSV-TTS-Lite model checksum mismatch')
    fingerprint_data = json.dumps({
        'model_family': manifest['model_family'],
        'runtime_version': manifest['runtime_version'],
        'checksums': checksums,
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    fingerprint = hashlib.sha256(fingerprint_data).hexdigest()[:24]
    if manifest.get('fingerprint') != fingerprint:
        raise ValueError('GSV-TTS-Lite model fingerprint mismatch')
    return fingerprint


def voice_catalog(root):
    """Only owner-installed, explicitly allowlisted voices can be selected."""
    path = Path(root) / 'voices.json'
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(rows, list) or len(rows) > 3:
        raise ValueError('Invalid local voice catalog')
    seen = set()
    for row in rows:
        if (set(row) != {'id', 'name', 'engine'} or row['id'] not in {'cyrene', 'yachiyo', 'xiaofei'}
                or row['id'] in seen or not isinstance(row['name'], str) or len(row['name']) > 40):
            raise ValueError('Invalid local voice catalog entry')
        seen.add(row['id'])
        if row['engine'].get('voice_id') != row['id']:
            raise ValueError('Voice ID differs from the configured engine')
    return rows


def load_engine(root):
    root = Path(root).resolve()
    path = root / 'engine.json'
    value = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'backend': 'gpt-sovits'}
    if not isinstance(value, dict) or set(value) - {'backend', 'package', 'fingerprint', 'voice_id'}:
        raise ValueError('Invalid owner engine configuration')
    preset_id = value.get('voice_id', 'cyrene')
    if preset_id not in {'cyrene', 'yachiyo', 'xiaofei'}:
        raise ValueError('Unknown local voice ID')
    backend = value.get('backend')
    if backend == 'gpt-sovits':
        if preset_id == 'xiaofei':
            raise ValueError('Xiaofei requires the verified GSV-TTS-Lite runtime')
        return {'backend': backend, 'command': [str(LEGACY_PYTHON), '-I',
                str(LEGACY_ROOT / 'api_v2.py'), '-a', '127.0.0.1', '-p', '9881', '-c', str(root / (preset_id + '.yaml'))],
                'signature': [str(LEGACY_ROOT / 'api_v2.py'), str(root / (preset_id + '.yaml'))],
                'cwd': str(LEGACY_ROOT), 'env': {}, 'fingerprint': 'gpt-sovits',
                'model_id': preset_id + '-v2proplus', 'preset_id': preset_id}
    if backend == 'gsv-lite':
        if preset_id != 'xiaofei' or value.get('package') != 'xiaofei-v2':
            raise ValueError('Unknown GSV-TTS-Lite model package')
        if not re.fullmatch(r'[0-9a-f]{24}', str(value.get('fingerprint', ''))):
            raise ValueError('Invalid GSV-TTS-Lite fingerprint')
        model_root = (root.parent / 'gsv-lite' / 'models').resolve()
        package = (model_root / value['package']).resolve()
        if not package.is_relative_to(model_root):
            raise ValueError('GSV-TTS-Lite package is outside the local model directory')
        fingerprint = gsv_lite_fingerprint(package)
        if fingerprint != value['fingerprint']:
            raise ValueError('GSV-TTS-Lite package differs from the selected configuration')
        python = root.parent / 'gsv-lite-env' / 'Scripts' / 'python.exe'
        entry = Path(__file__).with_name('gsv_lite_voice_server.py')
        if not python.is_file() or not entry.is_file():
            raise ValueError('GSV-TTS-Lite local runtime is incomplete')
        return {'backend': backend, 'command': [str(python), '-X', 'utf8', '-I', str(entry),
                '--runtime', str(root), '--package', value['package'], '--port', '9881'],
                'signature': [str(entry), value['package'], '9881'],
                'cwd': str(root.parent / 'gsv-lite'), 'env': {'GSV_TTS_LITE_VERSION': GSV_LITE_VERSION},
                'fingerprint': fingerprint, 'model_id': 'xiaofei-gsv-tts-lite-v2',
                'voice_id': preset_id, 'preset_id': preset_id}
    if backend != 'aniflive':
        raise ValueError('Unknown owner-selected voice engine')
    if preset_id == 'xiaofei':
        raise ValueError('Xiaofei is not packaged for AnifLive TensorRT')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', str(value.get('package', ''))):
        raise ValueError('Invalid local package name')
    trial = root.parent / 'aniflive-trial'
    package = (trial / 'models' / value['package']).resolve()
    if not package.is_relative_to((trial / 'models').resolve()):
        raise ValueError('Model package is outside the local model directory')
    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
    fingerprint = value.get('fingerprint', '')
    if (not re.fullmatch(r'[0-9a-f]{24}', fingerprint)
            or manifest.get('active_engine_fingerprint') != fingerprint
            or manifest.get('backend') != 'TensorRT-11'
            or manifest.get('model_family') != 'gsv-v2proplus'
            or manifest.get('default_voice_profile') != 'default'):
        raise ValueError('Local engine package differs from the verified configuration')
    python, entry = trial / 'env/Scripts/python.exe', trial / 'run_anif.py'
    if not python.is_file() or not entry.is_file():
        raise ValueError('AnifLive local runtime is incomplete')
    plan = json.loads((trial / 'trial-plan.json').read_text(encoding='utf-8'))
    return {'backend': backend, 'command': [str(python), '-X', 'utf8', '-I', str(entry),
            'serve', '--model-package', str(package), '--shared-dir', plan['shared_dir'],
            '--host', '127.0.0.1', '--port', '9881'],
            'signature': [str(entry), str(package), '9881'], 'cwd': str(trial),
            'env': {'ANIFLIVE_TRIAL_PACKAGE': str(package)}, 'fingerprint': fingerprint,
            'model_id': manifest['model_id'], 'voice_id': 'default', 'preset_id': preset_id}


async def tempo_wav(data, speed, max_bytes):
    """Pitch-preserving rate adaptation; output is an honest finite-length WAV."""
    if speed == 1:
        return bytes(data)
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise ValueError('FFmpeg is required for AnifLive speech-rate adaptation')
    with wave.open(io.BytesIO(data)) as source:
        rate = source.getframerate()
        if source.getsampwidth() != 2 or not 8000 <= rate <= 48000:
            raise ValueError('Unsupported model WAV parameters')
        if source.getnframes() / speed * 2 + 44 > max_bytes:
            raise ValueError('Rate-adjusted audio would exceed the output limit; not truncated')
    process = await asyncio.create_subprocess_exec(ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error',
        '-threads', '1', '-filter_threads', '1', '-protocol_whitelist', 'file,pipe',
        '-f', 'wav', '-i', 'pipe:0', '-af', f'atempo={speed:.8f}', '-ac', '1', '-ar', str(rate),
        '-fs', str(max_bytes - 44), '-f', 's16le', '-c:a', 'pcm_s16le', 'pipe:1',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
    try:
        pcm, _ = await asyncio.wait_for(process.communicate(data), 30)
        if process.returncode or not pcm or len(pcm) + 44 >= max_bytes or len(pcm) % 2:
            raise ValueError('Local speech-rate adaptation failed or exceeded its size limit')
        output = io.BytesIO()
        with wave.open(output, 'wb') as target:
            target.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
            target.writeframes(pcm)
        return output.getvalue()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', default='D:/appData/qqbot/voise/runtime')
    args = parser.parse_args()
    spec = load_engine(args.runtime)
    raise SystemExit(subprocess.call(spec['command'], cwd=spec['cwd'], env={**os.environ, **spec['env']},
        **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {})))
