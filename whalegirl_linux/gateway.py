from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
import uuid
from urllib.parse import quote, urlsplit

import httpx
from websockets.asyncio.client import connect


@dataclass
class Receipt:
    confirmed: bool
    message_id: str = ""
    error: str = ""
    definite_failure: bool = False
    quoted: bool = False
    quote_error: str = ""
    mentioned_ids: tuple[str, ...] = ()
    mention_error: str = ""


class UIJobError(ValueError):
    def __init__(self,message,state=''):
        super().__init__(message)
        self.state=state


def message_id(message):
    # Local IDs can restart for another account; the caller scopes by account and chat.
    return str(message.get("serverId") or f"local:{message.get('localId')}")


def server_acknowledged(message):
    value=str(message.get('serverId') or '')
    return value.isdigit() and int(value)>0 and message.get('nativeStatus')!=5


def outbound_identity(message):
    # serverId changes from zero to its final value after network acceptance.
    return ('local:'+str(message['localId'])) if message.get('localId') else message_id(message)


class AgentGateway:
    def __init__(self, url, token, *, client=None, control_directory=None):
        parsed = urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("微信网关只允许本机回环地址")
        self.url, self.token = url.rstrip("/"), token
        self.client = client or httpx.AsyncClient(base_url=self.url, headers={"Authorization": f"Bearer {token}"}, timeout=35, trust_env=False)
        self.login = {"state": "idle", "qrDataUrl": "", "message": "点击获取二维码"}
        self.login_task = None
        self.control_directory = Path(control_directory) if control_directory else None
        self.ui_lock = asyncio.Lock()
        self.inflight_sends = set()
        job_path=os.environ.get('WHALEGIRL_UI_JOBS_DIR')
        self.ui_jobs_directory=Path(job_path) if job_path else None
        self.native_reader = None
        self.native_reader_endpoint = None
        self._status_task = None
        self._status_cache = None
        self._status_at = 0.0
        self._members_cache = {}

    async def ui_job(self,operation,chat,**values):
        if not self.ui_jobs_directory:return {}
        from .settings import private_write
        nonce=uuid.uuid4().hex;path=self.ui_jobs_directory/(nonce+'.json')
        private_write(path,json.dumps({'operation':operation,'chat':chat,'created':time.time(),**values},ensure_ascii=False))
        response=await self.client.post(f"/api/chats/{quote(chat+'::whalejob::'+nonce,safe='')}/open",params={'clearUnreads':'false'},timeout=35)
        response.raise_for_status();value=response.json()
        if value.get('ok') is not True:
            saved=json.loads(path.read_text())
            raise UIJobError(str(saved.get('error') or value.get('error') or 'Native UI job failed')[:200],saved.get('state',''))
        return json.loads(path.read_text())

    def warm_send_status(self):
        # One account observation overlaps model generation. Polling and the
        # send path share it instead of taking simultaneous full UI snapshots.
        if self._status_cache is not None and time.monotonic()-self._status_at < 1:
            return
        if self._status_task is None or self._status_task.done():
            self._status_task = asyncio.create_task(self._read_status())
            self._status_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())

    async def status(self, *, max_age=0):
        if max_age > 0 and self._status_cache is not None and time.monotonic()-self._status_at < max_age:
            return dict(self._status_cache)
        if self._status_task is None or self._status_task.done():
            self._status_task = asyncio.create_task(self._read_status())
        return dict(await asyncio.shield(self._status_task))

    async def status_for_send(self):
        return await self.status(max_age=2)

    async def _read_status(self):
        response = await self.client.get("/api/status/auth")
        response.raise_for_status()
        value = response.json()
        if value.get("status") == "logged_in" and value.get("loggedInUser"):
            self.login.update(state="login_success", message="微信已登录", qrDataUrl="")
        # Recover native warnings after a core restart or an expired observer.
        if value.get("status") == "logged_out" and (not self.login_task or self.login_task.done()):
            try:
                tree_response = await self.client.get("/api/debug/a11y")
                tree_response.raise_for_status()
                popup = self.popup_text(tree_response.json().get("tree"))
                if popup:
                    self.login.update(state="attention", message=popup, qrDataUrl="")
            except (httpx.HTTPError, ValueError):
                pass
        self._status_cache = value
        self._status_at = time.monotonic()
        return value

    async def moments(self, operation, account, **values):
        if not self.ui_jobs_directory:
            raise ValueError('此网关尚未配置原生朋友圈适配')
        async with self.ui_lock:
            status = await self.status()
            if status.get('status') != 'logged_in' or status.get('loggedInUser') != account:
                raise UIJobError('朋友圈账号当前未登录', 'failed_before_send')
            return await self.ui_job(operation, account, **values)

    async def chats(self):
        result = []
        for offset in range(0, 1000, 100):
            response = await self.client.get("/api/chats", params={"limit": 100, "offset": offset})
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list):
                raise ValueError("微信网关会话格式错误")
            result.extend(rows)
            if len(rows) < 100:
                break
        return result

    async def messages(self, chat, limit=100, offset=0, **cursor):
        if self.ui_jobs_directory:
            try:
                return (await self.native_request('/messages',chat=chat,limit=limit,offset=offset,**cursor))['messages']
            except (httpx.HTTPError,OSError,ValueError,KeyError):
                # The UI only exposes a partial window. Falling back silently
                # would let its newest row advance the complete native cursor.
                raise
        response = await self.client.get(f"/api/messages/{quote(chat, safe='')}", params={"limit": limit, "offset": offset})
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, list):
            raise ValueError("微信网关消息格式错误")
        if cursor.get('server_id') is not None:
            value=[m for m in value if str(m.get('serverId'))==str(cursor['server_id'])]
        if cursor.get('after_time') is not None:
            from .runtime import timestamp
            boundary=(float(cursor['after_time']),int(cursor.get('after_id',0)))
            value=[m for m in value if (timestamp(m.get('timestamp')),m.get('localId',0))>boundary]
            value.sort(key=lambda m:(timestamp(m.get('timestamp')),m.get('localId',0)))
        if cursor.get('before_time') is not None:
            from .runtime import timestamp
            boundary=(float(cursor['before_time']),int(cursor.get('before_id',0)))
            value=[m for m in value if (timestamp(m.get('timestamp')),m.get('localId',0))<boundary]
        return value

    async def referenced_message(self,chat,server_id):
        if not str(server_id).isdigit():return None
        rows=await self.messages(chat,limit=1,server_id=str(server_id))
        return next((m for m in rows if str(m.get('serverId'))==str(server_id)),None)

    async def group_members(self,chat):
        if not chat.endswith('@chatroom') or not self.ui_jobs_directory:return []
        cached = self._members_cache.get(chat)
        if cached and time.monotonic()-cached[0]<60:return cached[1]
        value = await self.native_request('/members',chat=chat)
        rows = value.get('members',[])
        self._members_cache[chat] = (time.monotonic(),rows)
        return rows

    async def native_request(self, route, **params):
        if not self.ui_jobs_directory:return {}
        name=(self.ui_jobs_directory/'native-read.endpoint').read_text().strip()
        if not re.fullmatch(r'native-read-\d+\.sock',name):raise ValueError('Invalid native read socket')
        endpoint=str(self.ui_jobs_directory/name)
        if endpoint!=self.native_reader_endpoint:
            if self.native_reader:await self.native_reader.aclose()
            self.native_reader=httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=endpoint),
                                                base_url='http://localhost',timeout=6,trust_env=False)
            self.native_reader_endpoint=endpoint
        response=await self.native_reader.get(route,params=params)
        response.raise_for_status()
        return response.json()

    async def media(self, chat, local_id, quality="thumbnail", kind=3,server_id=''):
        if kind==3 and self.ui_jobs_directory:
            async with self.ui_lock:
                moved=False
                try:
                    result=await self.ui_job('read_image',chat,local_id=int(local_id),server_id=str(server_id))
                    for attempt in range(4):
                        if result.get('data') or result.get('quality') not in ('not_visible','partly_visible'):break
                        moved=True
                        await self.ui_job('open_chat',chat,scroll='latest' if attempt==0 else 'up')
                        result=await self.ui_job('read_image',chat,local_id=int(local_id),server_id=str(server_id))
                finally:
                    if moved:
                        try:await self.ui_job('open_chat',chat,scroll='latest')
                        except (httpx.HTTPError,OSError,ValueError):pass
            if result.get('data'):
                return {'type':'image','format':'png','data':result['data'],'quality':'native_clipboard_fast','partial':False}
            return {'type':'image','data':'','quality':result.get('quality') or 'clipboard_unavailable'}
        if kind==47 and self.ui_jobs_directory:
            try:
                value=await self.native_request('/media',chat=chat,local_id=int(local_id))
                if value.get('data'):return value
            except (httpx.HTTPError,OSError,ValueError,KeyError):
                pass
        response = await self.client.get(f"/api/messages/{quote(chat, safe='')}/media/{int(local_id)}",
                                         params={"quality": quality}, timeout=2)
        response.raise_for_status()
        return response.json()

    async def visible_media(self, chat, local_id, kind, **source):
        from .visible_media import capture_message_image
        async with self.ui_lock:
            self.media_history_moved=False
            try:return await capture_message_image(self,chat,local_id,kind,**source)
            finally:
                if self.ui_jobs_directory and self.media_history_moved:
                    await self.ui_job('open_chat',chat,scroll='latest')

    async def collect_sticker(self, chat, local_id, server_id, md5):
        if not re.fullmatch('[0-9a-f]{32}',md5) or not str(server_id).isdigit():
            raise ValueError('Invalid native sticker identity')
        async with self.ui_lock:
            return await self.ui_job('collect_sticker',chat,local_id=int(local_id),server_id=str(server_id),md5=md5)

    async def send_sticker(self, chat, md5):
        before={outbound_identity(m) for m in await self.messages(chat)}
        async def post_once():
            async with self.ui_lock:
                try:
                    result=await self.ui_job('send_sticker',chat,md5=md5)
                    return None if result.get('state')=='submitted' else Receipt(False,error='Native sticker did not submit')
                except UIJobError as exc:
                    return Receipt(False,error=str(exc),definite_failure=exc.state=='failed_before_send')
                except (httpx.HTTPError, ValueError, OSError):
                    return Receipt(False,error='Native sticker send was not confirmed')
        request=asyncio.create_task(post_once());self.inflight_sends.add(request)
        request.add_done_callback(self.inflight_sends.discard)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            try:
                rows=await self.messages(chat)
            except (httpx.HTTPError,ValueError,OSError):
                rows=[]
            for item in rows:
                if (item.get('isSelf') is True and item.get('type')==47 and
                        (item.get('sticker') or {}).get('md5')==md5 and outbound_identity(item) not in before):
                    if server_acknowledged(item):return Receipt(True,message_id(item))
                    if item.get('nativeStatus')==5:
                        return Receipt(False,error='微信报告表情发送失败，未获得服务器回执',definite_failure=True)
            if request.done() and request.result():
                return request.result()
            await asyncio.sleep(.15)
        return Receipt(False,error='Native sticker was not read back; do not resend automatically')

    async def send_image(self, chat, asset_id):
        if not self.ui_jobs_directory or not re.fullmatch('[0-9a-f]{64}',str(asset_id)):
            return Receipt(False,error='原生图片发送尚未配置',definite_failure=True)
        async with self.ui_lock:
            before={outbound_identity(m) for m in await self.messages(chat)}
            try:
                result=await self.ui_job('send_image',chat,asset_id=asset_id)
            except UIJobError as exc:
                return Receipt(False,error=str(exc),definite_failure=exc.state=='failed_before_send')
            except (httpx.HTTPError, ValueError, OSError):
                return Receipt(False,error='图片发送提交结果待核实，不自动重发')
            if result.get('state')!='submitted':
                return Receipt(False,error='原生图片未确认提交')
            deadline=time.monotonic()+25
            while time.monotonic()<deadline:
                try:
                    rows=await self.messages(chat)
                except (httpx.HTTPError,ValueError,OSError):
                    rows=[]
                fresh=[m for m in rows if m.get('isSelf') is True and m.get('type')==3
                       and outbound_identity(m) not in before]
                if len(fresh)==1:
                    item=fresh[0]
                    if item.get('nativeStatus')==5:
                        return Receipt(False,error='微信报告图片发送失败',definite_failure=True)
                    if server_acknowledged(item):
                        return Receipt(True,message_id(item))
                elif len(fresh)>1:
                    return Receipt(False,error='出现多条新图片，无法唯一确认，请人工核对')
                await asyncio.sleep(.2)
            return Receipt(False,error='未读到微信图片服务器回执，不自动重发')

    async def send(self, chat, text, *, quote_to=None, mentions=None):
        mentions = list(mentions or [])[:3] if chat.endswith('@chatroom') else []
        if mentions and not self.ui_jobs_directory:
            return Receipt(False,error='当前网关不支持原生@成员',definite_failure=True)
        before = {outbound_identity(m) for m in await self.messages(chat)}
        submission = {}
        async def post_once():
            async with self.ui_lock:
                try:
                    if self.ui_jobs_directory:
                        result=await self.ui_job('send_text',chat,text=text,**({'quote_to':quote_to} if quote_to else {}),
                                                **({'mentions':mentions} if mentions else {}))
                        submission.update(result)
                        return None if result.get('state')=='submitted' else Receipt(False,error='UI job did not submit text')
                    response = await self.client.post("/api/messages/send", json={"chatId": chat, "text": text}, timeout=45)
                    response.raise_for_status()
                    value = response.json()
                    return None if value.get("success") is True else Receipt(False,error=str(value.get("error") or "微信发送未确认")[:240])
                except UIJobError as exc:
                    return Receipt(False,error=str(exc),definite_failure=exc.state=='failed_before_send')
                except (httpx.HTTPError, ValueError, OSError):
                    return Receipt(False,error="发送请求超时或连接异常")
        request = asyncio.create_task(post_once())
        self.inflight_sends.add(request)
        request.add_done_callback(self.inflight_sends.discard)
        # Post exactly once, then only read. An uncertain result must never trigger a retry.
        # Read concurrently: the native plan can finish bookkeeping after the message is delivered.
        deadline = time.monotonic() + 50
        while time.monotonic() < deadline:
            try:
                for item in await self.messages(chat):
                    mid = message_id(item)
                    content = item.get('content','').strip()
                    mentioned = tuple(item.get('mentionedUserIds') or ())
                    expected_mentions = {m['id'] for m in mentions}
                    matches_text = content == text.strip() or bool(mentions and content.endswith(text.strip()) and
                        expected_mentions.issubset(mentioned))
                    if item.get("isSelf") is True and matches_text and outbound_identity(item) not in before:
                        if item.get('nativeStatus')==5:
                            return Receipt(False,error='微信报告文字发送失败，未获得服务器回执',definite_failure=True)
                        if not server_acknowledged(item):continue
                        reference = item.get('reply') or {}
                        if quote_to and reference.get('serverId'):
                            if str(reference['serverId']) != str(quote_to['server_id']):
                                return Receipt(False,error='Quoted reply was sent to a different source message; do not resend')
                            return Receipt(True,mid,quoted=True,mentioned_ids=mentioned,
                                mention_error=submission.get('mention_fallback',''))
                        return Receipt(True,mid,quote_error=(submission.get('quote_fallback') or 'Native quote was not applied') if quote_to else '',
                            mentioned_ids=mentioned,mention_error=submission.get('mention_fallback','') or
                            ('已发送文字，但微信未确认@提醒；不自动重发' if mentions and not expected_mentions.issubset(mentioned) else ''))
            except (httpx.HTTPError, ValueError):
                pass
            if request.done() and request.result() is not None:
                return request.result()
            await asyncio.sleep(.3)
        result = request.result() if request.done() and not request.cancelled() else None
        return result or Receipt(False, error="网关返回成功，但未读回新消息；请人工核对，不自动重发")

    async def start_login(self):
        if self.login_task and not self.login_task.done():
            if not self.control_directory:
                return self.login
            self.login_task.cancel()
            await asyncio.gather(self.login_task, return_exceptions=True)
        self.login = {"state": "starting", "qrDataUrl": "", "message": "正在获取二维码"}
        if self.control_directory:
            from .settings import private_write
            nonce = uuid.uuid4().hex
            private_write(self.control_directory / "qr-refresh.request", json.dumps({"nonce": nonce}))
            response_file = self.control_directory / "qr-refresh.response"
            for _ in range(100):
                if response_file.exists():
                    result = json.loads(response_file.read_text())
                    if result.get("nonce") == nonce:
                        if not result.get("ok"):
                            self.login.update(state="error", message=result.get("message", "二维码刷新失败"))
                            return self.login
                        break
                await asyncio.sleep(0.3)
            else:
                self.login.update(state="error", message="二维码刷新服务未响应")
                return self.login
        self.login_task = asyncio.create_task(self._observe_login())
        return self.login

    @staticmethod
    def popup_text(tree):
        def walk(node):
            yield node
            for child in node.get("children") or []:
                yield from walk(child)
        candidates = []
        for node in walk(tree or {}):
            # Qt also exposes top-level warnings as sibling filler containers.
            if node.get("role") not in ("frame", "dialog", "alert", "filler", "layered-pane", "panel", "window"):
                continue
            children = list(walk(node))
            if any(c.get("role") == "push-button" and re.fullmatch(r"OK|Confirm|确定|确认", c.get("name", ""), re.I) for c in children):
                labels = [c.get("name", "").strip() for c in children if c.get("role") in ("label", "static") and c.get("name", "").strip() and not re.fullmatch(r"Tip|OK|Confirm|确定|确认", c.get("name", "").strip(), re.I)]
                if labels:
                    candidates.append((len(children), "\n".join(dict.fromkeys(labels))[:800]))
        return min(candidates)[1] if candidates else ""

    async def safety_notice(self):
        response = await self.client.get('/api/debug/a11y', timeout=5)
        response.raise_for_status()
        return self.popup_text(response.json().get('tree') or {})

    async def _observe_login(self):
        """Observe native login without auto-confirming unknown client warnings."""
        try:
            native = await self.client.get('/api/debug/a11y')
            native.raise_for_status()
            def phone_confirmation(tree):
                def nodes(item):
                    yield item
                    for child in item.get('children') or []:
                        yield from nodes(child)
                return any(n.get('role')=='label' and
                           re.search(r'confirm on phone|comfirm on phone|手机确认',n.get('name',''),re.I)
                           for n in nodes(tree or {}))
            if phone_confirmation(native.json().get('tree')):
                self.login.update(state='phone_confirmation',qrDataUrl='',message='请在手机微信确认这次 Linux 登录')
            else:
                response = await self.client.post("/api/status/login")
                response.raise_for_status()
                initial = response.json()
                self.login.update(state="qr", qrDataUrl=initial.get("qrDataUrl", ""), message="请用微信小号扫码并在手机确认")
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                tree_response = await self.client.get("/api/debug/a11y")
                tree_response.raise_for_status()
                tree = tree_response.json().get('tree')
                popup = self.popup_text(tree)
                if popup:
                    self.login.update(state="attention", message=popup, qrDataUrl="")
                    return
                if phone_confirmation(tree) and self.login.get('state')!='phone_confirmation':
                    self.login.update(state='phone_confirmation',qrDataUrl='',message='请在手机微信确认这次 Linux 登录')
                status = await self.status()
                if status.get("status") == "logged_in":
                    if status.get("loggedInUser"):
                        self.login.update(state="login_success", message="微信已登录", qrDataUrl="")
                    else:
                        # Register the account only after the native chat window is visible.
                        await self._login_stream()
                    return
                await asyncio.sleep(1.5)
            self.login.update(state="login_timeout", message="二维码等待超时，请手动重新获取", qrDataUrl="")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.login.update(state="error", message=f"登录观察异常：{type(exc).__name__}")

    async def _login_stream(self):
        try:
            async with connect(self.url.replace("http://", "ws://", 1) + "/api/ws/login?timeoutMs=240000",
                               additional_headers={"Authorization": f"Bearer {self.token}"}, proxy=None,
                               max_size=4 * 1024 * 1024, open_timeout=15) as socket:
                async for raw in socket:
                    event = json.loads(raw)
                    kind = event.get("type", "status")
                    self.login["state"] = kind
                    if event.get("qrDataUrl"):
                        self.login["qrDataUrl"] = event["qrDataUrl"]
                    if event.get("message"):
                        self.login["message"] = str(event["message"])[:200]
                    if kind == "login_success":
                        self.login.update(qrDataUrl="", message="微信已登录")
                        return
                    if kind in ("login_timeout", "error"):
                        return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.login.update(state="error", message=f"登录通道异常：{type(exc).__name__}")

    async def close(self):
        if self.login_task:
            self.login_task.cancel()
            await asyncio.gather(self.login_task, return_exceptions=True)
        if self.inflight_sends:
            await asyncio.gather(*self.inflight_sends,return_exceptions=True)
        if self._status_task:
            await asyncio.gather(self._status_task, return_exceptions=True)
        if self.native_reader:
            await self.native_reader.aclose()
        await self.client.aclose()
