"""Nyro audio protocols. Paid POSTs are attempted once, downloads carry no key."""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import re
import secrets
import socket
import time
from urllib.parse import urljoin, urlsplit

import httpx


class AudioError(ValueError):
    pass


class GenerationUnknown(AudioError):
    pass


def result_audio(value):
    if not isinstance(value, dict):
        raise AudioError('音频接口返回结构无效')
    for item in value.get('data') or []:
        if isinstance(item, dict) and isinstance(item.get('url'), str):
            return {'url': item['url']}
    for choice in value.get('choices') or []:
        message = choice.get('message', {}) if isinstance(choice, dict) else {}
        audio = message.get('audio') or {}
        if isinstance(audio, dict):
            if isinstance(audio.get('url'), str):
                return {'url': audio['url']}
            if isinstance(audio.get('data'), str):
                # Require a container header later; raw PCM is not guessed.
                return {'base64': audio['data']}
        content = message.get('content')
        if isinstance(content, str):
            found = re.search(r'\[[^\]\n]*\]\((https://[^\s)]+)\)', content)
            if found:
                return {'url': found[1]}
    raise AudioError('接口没有返回可识别的音频结果，未自动重新生成')


async def public_destination(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.fragment or parsed.port not in (None, 443)):
        raise AudioError('音频下载地址不符合 HTTPS 公网要求')
    try:
        addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, 443, type=socket.SOCK_STREAM)
        ips = list(dict.fromkeys(item[4][0] for item in addresses))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise AudioError('音频下载地址解析到非公网，已拒绝')
    except (OSError, ValueError):
        raise AudioError('音频下载地址无法安全解析') from None
    return parsed.hostname, ips[0]


class AudioProviderClient:
    def __init__(self, *, transport=None, download_transport=None, resolver=public_destination):
        self.client = httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                                       timeout=httpx.Timeout(300, connect=10))
        self.download_client = httpx.AsyncClient(transport=download_transport, trust_env=False,
            follow_redirects=False, timeout=httpx.Timeout(40, connect=10))
        self.resolver = resolver
        self._pc_connected = False
        self._pc_checked = 0.0
        self._pc_presets = None
        self._pc_languages = []

    async def close(self):
        await self.client.aclose()
        await self.download_client.aclose()

    async def generate(self, capture, kind, arguments):
        provider, settings = capture['provider'], capture['settings']
        if provider.backend == 'pc_bridge':
            return await self._pc_voice(capture, kind, arguments)
        payload = {'model': provider.model, 'stream': False}
        if kind == 'speech':
            payload.update(messages=[{'role': 'user', 'content': arguments['text']}], modalities=['audio'],
                           audio={'voice': capture['preset'].voice, 'format': 'wav'})
        else:
            prompt = arguments['prompt']
            if not arguments.get('instrumental') and settings.singing_style:
                prompt += '\n演唱偏好：' + settings.singing_style
            payload.update(messages=[{'role': 'user', 'content': prompt}], n=1,
                instrumental=arguments.get('instrumental', False), duration=arguments.get('duration') or settings.music_duration,
                audio_format='mp3')
            if arguments.get('lyrics') and not payload['instrumental']:
                payload['lyrics'] = arguments['lyrics']
        started = time.monotonic()
        try:
            async with self.client.stream('POST', provider.endpoint_url(), json=payload,
                    headers={'Authorization': 'Bearer ' + capture['key']},
                    timeout=httpx.Timeout(settings.request_timeout, connect=10)) as response:
                if response.status_code >= 500:
                    raise GenerationUnknown('音频生成接口异常，是否已生成或扣费待核对，未自动重试')
                if response.status_code != 200:
                    raise AudioError('音频生成接口 HTTP ' + str(response.status_code))
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 24 * 1024**2:
                        raise AudioError('音频结果响应超出解析上限')
            value = json.loads(body)
            result = result_audio(value)
            usage = value.get('usage')
            result.update(usage=usage if isinstance(usage, dict) else {},
                          generation_seconds=round(time.monotonic() - started, 3))
            return result
        except (httpx.TimeoutException, httpx.NetworkError):
            raise GenerationUnknown('生成请求超时或中断，可能已扣费，未自动重新生成') from None
        except (TypeError, json.JSONDecodeError):
            raise AudioError('音频接口返回内容无法解析，未自动重新生成') from None

    async def pc_status(self, capture):
        if capture['provider'].backend != 'pc_bridge':
            return {'connected': False, 'reason': '当前语音配置使用云端模型'}
        try:
            nonce = secrets.token_hex(16)
            response = await self.client.get(capture['provider'].api_url + '/health',
                headers={'Authorization': 'Bearer ' + capture['key'], 'X-Voice-Handshake': nonce}, timeout=4)
            value = response.json() if response.status_code == 200 and len(response.content) < 4096 else {}
            if not isinstance(value, dict) or value.get('handshake_nonce') != nonce or not re.fullmatch('[0-9a-f]{32}', str(value.get('instance_id', ''))):
                raise ValueError('Invalid PC voice handshake')
            connected = not value.get('stopping') and (value.get('ready') is True or value.get('busy') is True)
            ack = await self.client.post(capture['provider'].api_url + '/v1/handshake/ack',
                headers={'Authorization': 'Bearer ' + capture['key']},
                json={'nonce': nonce, 'instance_id': value['instance_id'], 'accepted': bool(connected)}, timeout=4)
            ack_value = ack.json() if ack.status_code == 200 and len(ack.content) < 2048 else {}
            if (not isinstance(ack_value, dict) or ack_value.get('acknowledged') is not True
                    or ack_value.get('instance_id') != value['instance_id'] or ack_value.get('accepted') is not bool(connected)):
                raise ValueError('PC voice handshake was not acknowledged')
            self._pc_connected, self._pc_checked = connected, time.monotonic()
            self._pc_presets = value.get('presets', [])
            self._pc_languages = value.get('languages', [])
            return {'connected': connected, 'busy': value.get('busy') is True,
                    'handshake_at': time.time(), 'instance_id': value['instance_id'],
                    'presets': value.get('presets', []),
                    'speech_controls': value.get('speech_controls', []),
                    'languages': value.get('languages', []),
                    'active_voice': value.get('active_voice', ''), 'voices': value.get('voices', []),
                    'voice_switch': value.get('voice_switch', {}), 'backend': value.get('backend', ''),
                    'reason': '' if connected else '电脑未连接、模型未就绪或通道认证失败'}
        except (httpx.HTTPError, ValueError):
            self._pc_connected, self._pc_checked = False, time.monotonic()
            return {'connected': False, 'reason': '电脑语音未连接；普通文字聊天不受影响'}

    def pc_ready(self):
        return self._pc_connected and time.monotonic() - self._pc_checked < 45

    def pc_presets(self):
        return self._pc_presets

    def pc_languages(self):
        return self._pc_languages

    async def select_pc_voice(self, capture, voice_id, request_id):
        if capture['provider'].backend != 'pc_bridge' or voice_id not in {'cyrene', 'yachiyo', 'xiaofei'}:
            raise AudioError('当前不是可切换的电脑声线')
        try:
            response = await self.client.post(capture['provider'].api_url + '/v1/voices/switch',
                headers={'Authorization': 'Bearer ' + capture['key']},
                json={'voice_id': voice_id, 'request_id': request_id}, timeout=8)
            if response.status_code != 200:
                raise AudioError('电脑声线未受理切换，请核对是否已安装或正在切换')
            result = response.json()
            if result.get('id') != request_id or result.get('voice_id') != voice_id:
                raise AudioError('切换结果标识不符，请核对实际状态')
            self._pc_connected = False
            return result
        except AudioError:
            raise
        except (httpx.HTTPError, ValueError):
            raise AudioError('电脑切换通道暂不可用，请核对状态；未自动重复提交') from None

    async def _pc_voice(self, capture, kind, arguments):
        if kind != 'speech' or not re.fullmatch('[0-9a-f]{32}', capture.get('request_id', '')):
            raise AudioError('电脑语音任务 ID 或类型无效')
        if len(arguments['text']) > 600:
            raise AudioError('电脑语音每次最多600字，请缩短后重新选择')
        # A stale SSH listener must not hold a new job for the inference timeout.
        # This health request never submits speech or calls a model.
        status = await self.pc_status(capture)
        if not status['connected']:
            raise AudioError('电脑语音未就绪，未提交合成；普通文字聊天继续运行')
        if status.get('busy'):
            raise AudioError('电脑正在合成另一段语音，本次未提交')
        if status.get('presets') and capture['preset'].voice not in status['presets']:
            raise AudioError('所选声线与电脑当前模型不同，请等待声线同步后重试')
        payload = {'request_id': capture['request_id'], 'text': arguments['text'],
                   'preset': capture['preset'].voice}
        language = arguments.get('language') or 'auto'
        if language in status.get('languages', []):
            payload['language'] = language
        elif language != 'auto':
            raise AudioError('电脑语音适配器尚不支持指定语种，未提交合成')
        native = capture.get('native_synthesis') or {}
        if native:
            if not set(native).issubset(set(status.get('speech_controls', []))):
                raise AudioError('电脑语音适配器尚未更新声音调节协议，未提交合成')
            payload.update(native)
        started = time.monotonic()
        try:
            async with self.client.stream('POST', capture['provider'].endpoint_url(), json=payload,
                    headers={'Authorization': 'Bearer ' + capture['key']},
                    timeout=httpx.Timeout(min(capture['settings'].request_timeout, 200), connect=3)) as response:
                if response.status_code in (409, 502, 504):
                    raise GenerationUnknown('电脑语音任务已执行或结果未确认，未自动重新合成')
                if response.status_code != 200:
                    raise AudioError('电脑语音暂不可用（HTTP ' + str(response.status_code) + '），普通聊天继续运行')
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > capture['settings'].max_download_mb * 1024**2:
                        raise AudioError('电脑返回音频超过文件上限')
            if data[:4] != b'RIFF' or data[8:12] != b'WAVE':
                raise AudioError('电脑未返回有效的 WAV 音频')
            if response.headers.get('X-Voice-ID', capture['preset'].voice) != capture['preset'].voice:
                raise GenerationUnknown('返回声线与任务绑定声线不同，保留待核对，未自动重新合成')
            return {'bytes': bytes(data), 'usage': {}, 'charge_state': 'local_no_api_charge',
                    'language': response.headers.get('X-Voice-Language', payload.get('language', 'zh')),
                    'generation_seconds': round(time.monotonic() - started, 3)}
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise AudioError('电脑语音未连接，未提交合成；普通文字聊天不受影响') from None
        except (httpx.TimeoutException, httpx.NetworkError):
            raise GenerationUnknown('电脑合成连接中断，结果待核对，未自动重新合成') from None

    async def download(self, result, path, cap):
        if 'bytes' in result:
            raw = result['bytes']
            if not isinstance(raw, bytes) or not raw or len(raw) > cap:
                raise AudioError('电脑音频为空或超过文件上限')
            path.write_bytes(raw)
            path.chmod(0o600)
            return
        if 'base64' in result:
            data = result['base64']
            if len(data) > (cap * 4 // 3 + 8):
                raise AudioError('内嵌音频超过大小限制')
            try:
                raw = base64.b64decode(data, validate=True)
            except ValueError:
                raise AudioError('内嵌音频编码无效') from None
            if not raw or len(raw) > cap:
                raise AudioError('音频为空或超过大小限制')
            path.write_bytes(raw)
            path.chmod(0o600)
            return
        # Retry only the already-created result, never the generation POST.
        for attempt in range(2):
            try:
                await self._download(result['url'], path, cap)
                return
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt:
                    raise AudioError('音频已生成但下载中断，可重试下载，未重新生成') from None

    async def _download(self, url, path, cap):
        for _ in range(4):
            host, ip = await self.resolver(url)
            # Pin the checked address; TLS still verifies the original hostname.
            target = httpx.URL(url).copy_with(host=ip)
            async with self.download_client.stream('GET', target, headers={'Host': host},
                    extensions={'sni_hostname': host}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get('location')
                    if not location:
                        raise AudioError('音频重定向地址缺失')
                    url = urljoin(url, location)
                    continue
                if response.status_code != 200:
                    raise AudioError('成品下载 HTTP ' + str(response.status_code) + '，未重新生成')
                try:
                    declared = int(response.headers.get('content-length', '0'))
                except ValueError:
                    raise AudioError('音频下载长度声明无效') from None
                if declared < 0 or declared > cap:
                    raise AudioError('音频文件超过下载上限')
                count = 0
                with path.open('wb') as output:
                    path.chmod(0o600)
                    async for chunk in response.aiter_bytes():
                        count += len(chunk)
                        if count > cap:
                            raise AudioError('音频文件超过下载上限')
                        output.write(chunk)
                if not count:
                    raise AudioError('下载音频为空')
                return
        raise AudioError('音频重定向次数过多')
