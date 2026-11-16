"""Audio delivery through the installed gateway, using its existing GUI lock."""
import asyncio
import base64
import re
import time
from urllib.parse import quote
import xml.etree.ElementTree as ET

import httpx

from .gateway import Receipt, outbound_identity, server_acknowledged


def file_matches(row, filename, size):
    if not row.get('isSelf') or row.get('type') != 49:
        return False
    # The installed gateway gives uploads a send_file_<timestamp>_ prefix.
    def same_name(name):
        return name == filename or bool(re.fullmatch(r'send_file_\d+_' + re.escape(filename), str(name or '')))
    if same_name(row.get('fileName')):
        try:
            return not row.get('fileSize') or int(row['fileSize']) == size
        except (ValueError, TypeError):
            return False
    content = str(row.get('content') or '')
    if len(content) > 100_000 or '<!DOCTYPE' in content or '<!ENTITY' in content:
        return False
    try:
        root = ET.fromstring(content)
        title = root.findtext('.//title')
        length = root.findtext('.//totallen')
        return (same_name(title) and root.findtext('.//type') == '6'
                and (not length or int(length) == size))
    except (ET.ParseError, ValueError):
        return False


class AudioGateway:
    def __init__(self, gateway):
        self.gateway = gateway

    async def voice_receipt(self, chat, native_id, value=None):
        if value is None:
            response = await self.gateway.client.get('/api/messages/voice/' + quote(native_id, safe=''), timeout=12)
            response.raise_for_status()
            value = response.json()
        if not isinstance(value, dict) or value.get('chatId', chat) != chat:
            return Receipt(False, error='语音任务会话不符，未重新发送', retryable=False)
        chunks = value.get('chunks') or []
        if not isinstance(chunks, list) or any(not isinstance(part, dict) for part in chunks):
            return Receipt(False, error='语音任务状态格式异常，需核对', retryable=False)
        details = {'kind': 'voice', 'native_job_id': native_id,
            'chunks': [{key: part.get(key) for key in ('index', 'status', 'messageId', 'decodedDurationSeconds')} for part in chunks]}
        if value.get('status') == 'completed':
            ids = {str(part.get('messageId')) for part in chunks if part.get('status') == 'verified' and part.get('messageId') is not None}
            rows = await self.gateway.messages(chat)
            acknowledged = {str(row.get('localId')) for row in rows
                if row.get('type') == 34 and row.get('isSelf') and server_acknowledged(row)}
            if ids and len(ids) == len(chunks) and ids <= acknowledged:
                return Receipt(True, ','.join(sorted(ids)), retryable=False, details=details)
            return Receipt(False, error='语音已录制发送，服务端回执待核对', retryable=False, details=details)
        if value.get('status') in ('failed', 'cancelled', 'needs_review'):
            possible = any(part.get('messageId') or part.get('status') in ('verified', 'send_clicked', 'unknown') for part in chunks)
            return Receipt(False, error='语音任务停止，' + ('已有或可能有分段发出，需核对' if possible else '未确认发出'),
                definite_failure=not possible and value.get('status') != 'needs_review', retryable=False, details=details)
        return None

    async def voice(self, chat, path, identity, allowed, on_submitted):
        gateway = self.gateway
        if not hasattr(gateway, 'ui_lock') or not hasattr(gateway, 'client'):
            return Receipt(False, error='当前网关不支持原生语音任务', definite_failure=True)
        native_id = ''
        async with gateway.ui_lock:
            if not allowed():
                return Receipt(False, error='语音发送权限已变化', definite_failure=True)
            try:
                with path.open('rb') as source:
                    response = await gateway.client.post('/api/messages/voice',
                        data={'chatId': chat}, files={'audio': ('speech.wav', source, 'application/octet-stream')},
                        headers={'Idempotency-Key': identity}, timeout=40)
                if response.status_code in (400, 401, 403, 404, 409, 413, 422):
                    return Receipt(False, error='语音任务未受理 HTTP ' + str(response.status_code), definite_failure=True)
                response.raise_for_status()
                value = response.json()
                native_id = str(value.get('jobId') or '')
                if not native_id:
                    return Receipt(False, error='语音受理结果缺少任务 ID，需核对', retryable=False)
                on_submitted(native_id)
                deadline = time.monotonic() + 110
                cancel_sent = False
                while time.monotonic() < deadline:
                    if not allowed() and not cancel_sent:
                        await gateway.client.post('/api/messages/voice/' + quote(native_id, safe='') + '/cancel', timeout=10)
                        cancel_sent = True
                    receipt = await self.voice_receipt(chat, native_id)
                    if receipt is not None:
                        return receipt
                    await asyncio.sleep(.6)
                return Receipt(False, error='语音发送状态等待超时，停止自动重发', retryable=False,
                               details={'kind': 'voice', 'native_job_id': native_id})
            except asyncio.CancelledError:
                if native_id:
                    try:
                        await asyncio.shield(gateway.client.post('/api/messages/voice/' + quote(native_id, safe='') + '/cancel', timeout=8))
                    except Exception:
                        pass
                raise
            except (httpx.HTTPError, ValueError, OSError):
                return Receipt(False, error='语音提交或查询中断，需核对后处理', retryable=False,
                               details={'kind': 'voice', 'native_job_id': native_id})

    async def file(self, chat, path, filename, allowed):
        gateway = self.gateway
        if not hasattr(gateway, 'ui_lock') or not hasattr(gateway, 'client'):
            return Receipt(False, error='当前网关没有文件发送能力', definite_failure=True)
        async with gateway.ui_lock:
            if not allowed():
                return Receipt(False, error='音频发送权限已变化', definite_failure=True)
            before = {outbound_identity(row) for row in await gateway.messages(chat)}
            size = path.stat().st_size
            try:
                # One native submission; an ambiguous reply never causes a resend.
                response = await gateway.client.post('/api/messages/send', json={'chatId': chat,
                    'file': {'data': base64.b64encode(path.read_bytes()).decode(), 'filename': filename}}, timeout=50)
                if response.status_code in (400, 401, 403, 404, 413, 422):
                    return Receipt(False, error='文件任务未受理 HTTP ' + str(response.status_code), definite_failure=True)
                response.raise_for_status()
                # Even success=false may follow a native send; verify rather than repeat.
            except (httpx.HTTPError, ValueError):
                pass
            deadline = time.monotonic() + 18
            while time.monotonic() < deadline:
                try:
                    matches = [row for row in await gateway.messages(chat)
                        if outbound_identity(row) not in before and file_matches(row, filename, size)]
                    if len(matches) == 1 and server_acknowledged(matches[0]):
                        return Receipt(True, outbound_identity(matches[0]), retryable=False,
                                       details={'kind': 'audio_file', 'filename': filename, 'size': size})
                except (httpx.HTTPError, ValueError):
                    pass
                await asyncio.sleep(.6)
            return Receipt(False, error='音频文件已提交，送达结果待核对，不自动补发', retryable=False)
