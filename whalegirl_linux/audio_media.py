"""Bounded local audio inspection and tuning, with no network-capable decoders."""
import asyncio
import json
import math
from pathlib import Path
import shutil
import wave

from .audio_provider import AudioError

MAX_OUTPUT_BYTES = 64 * 1024**2


def process_limits():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (40, 45))
        # Ubuntu FFmpeg maps many shared libraries before decoding; virtual
        # address space needs room for those even when resident usage is small.
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024**2, 64 * 1024**2))
    except (ImportError, ValueError):
        pass


async def run(args, timeout=45):
    import os
    environment = {**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
    process = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=environment,
        **({'preexec_fn': process_limits} if os.name == 'posix' else {}))
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout)
        if process.returncode or len(stdout) > 128 * 1024:
            raise AudioError('音频处理失败，请检查格式与资源状态')
        return stdout
    except asyncio.TimeoutError:
        raise AudioError('本地音频处理超时') from None
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def probe(path, *, max_seconds=600):
    path = Path(path)
    with path.open('rb') as source:
        header = source.read(16)
    if header.startswith(b'RIFF') and header[8:12] == b'WAVE':
        try:
            with wave.open(str(path), 'rb') as audio:
                duration = audio.getnframes() / audio.getframerate()
                if not 1 <= audio.getnchannels() <= 2 or not 8000 <= audio.getframerate() <= 192000:
                    raise AudioError('音频采样参数超限')
                expected = audio.getnframes() * audio.getnchannels() * audio.getsampwidth()
                # RIFF claiming more PCM than was downloaded is not valid media.
                if expected > path.stat().st_size:
                    raise AudioError('WAV 音频内容不完整')
                result = {'format': 'wav', 'duration': duration, 'sample_rate': audio.getframerate()}
        except (wave.Error, EOFError, ZeroDivisionError):
            raise AudioError('WAV 音频无法解码') from None
    else:
        if not (header.startswith((b'ID3', b'\xff')) or header[4:8] == b'ftyp'):
            raise AudioError('返回内容不是支持的音频容器')
        ffprobe = shutil.which('ffprobe')
        if not ffprobe:
            raise AudioError('非 WAV 音频需要 FFprobe，当前环境未安装')
        value = json.loads(await run([ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe',
            '-select_streams', 'a:0', '-show_entries', 'format=duration,format_name:stream=sample_rate,channels',
            '-of', 'json', str(path)], 8))
        try:
            stream = value['streams'][0]
            if not 1 <= int(stream['channels']) <= 2:
                raise ValueError()
            result = {'format': 'm4a' if header[4:8] == b'ftyp' else 'mp3',
                'duration': float(value['format']['duration']), 'sample_rate': int(stream['sample_rate'])}
        except (KeyError, IndexError, TypeError, ValueError):
            raise AudioError('音频元数据无效') from None
    if not math.isfinite(result['duration']) or not .05 <= result['duration'] <= max_seconds:
        raise AudioError('音频实际时长为空或超出单次处理范围')
    return result


def tuning_filter(preset):
    factor = 2 ** (preset.pitch / 12)
    return (f'aresample=48000,asetrate={48000 * factor:.6f},aresample=48000,'
            f'atempo={preset.speed / factor:.8f},volume={preset.gain_db:.4f}dB,alimiter=limit=0.95')


async def tune(source, destination, preset):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise AudioError('声线调节需要 FFmpeg，当前环境未安装')
    original = await probe(source)
    if original['duration'] / preset.speed * 48000 * 2 + 4096 > MAX_OUTPUT_BYTES:
        raise AudioError('调参后的音频超过单次转码空间限制，未截断音频')
    await run([ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-threads', '1',
        '-filter_threads', '1', '-protocol_whitelist', 'file,pipe', '-i', str(source),
        '-vn', '-ac', '1', '-af', tuning_filter(preset), '-ar', '48000', '-c:a', 'pcm_s16le',
        '-n', str(destination)])
    destination.chmod(0o600)
    return await probe(destination)
