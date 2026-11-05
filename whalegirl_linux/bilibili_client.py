"""Bounded read-only Bilibili client. Authentication never enters model material."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .settings import private_write

API = 'https://api.bilibili.com'
PASSPORT = 'https://passport.bilibili.com'
PATHS = frozenset({
    '/x/web-interface/nav', '/x/web-interface/view', '/x/player/wbi/v2', '/x/web-interface/popular',
    '/x/web-interface/wbi/index/top/feed/rcmd', '/x/web-interface/wbi/search/type',
    '/x/polymer/web-dynamic/v1/feed/all', '/x/web-interface/history/cursor',
    '/x/v3/fav/folder/created/list-all', '/x/v3/fav/resource/list',
    '/x/v2/reply/main', '/x/v1/dm/list.so', '/x/player/videoshot',
    '/x/v2/reply/reply', '/x/player/wbi/playurl',
})
LOGIN_PATHS = frozenset({'/x/passport-login/web/qrcode/generate', '/x/passport-login/web/qrcode/poll',
                         '/x/passport-login/web/crossDomain'})
COOKIE_NAMES = frozenset({'SESSDATA', 'bili_jct', 'DedeUserID', 'DedeUserID__ckMd5', 'buvid3', 'buvid4', 'sid'})
RISK_CODES = frozenset({-352, -403, -412, -509, -799, -7992})
LOGIN_CODES = frozenset({-101, -102, -111})
MIXIN = (46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,
         37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52)


class BiliError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class PrivacyFilter(logging.Filter):
    def filter(self, record):
        text = record.getMessage().casefold()
        return not any(word in text for word in ('passport.bilibili.com', 'qrcode_key', 'crossdomain',
                                                 'sessdata', 'bili_jct', 'set-cookie', 'dedeuserid',
                                                 '.bilivideo.', 'auth_key=', 'upsig=', 'deadline='))


def signed(params, images, now=None):
    """Public WBI signing material; adapted from humanism lib/wbi.mjs (MIT)."""
    keys = []
    for name in ('img_url', 'sub_url'):
        url = urlsplit(str(images.get(name) or ''))
        stem = url.path.rsplit('/', 1)[-1].split('.')[0]
        if url.scheme != 'https' or url.username or url.password or not re.fullmatch('[a-f0-9]{32}', stem):
            raise BiliError('signing_unavailable', '暂未取得 B 站公开签名材料')
        keys.append(stem)
    raw = ''.join(keys)
    mix = ''.join(raw[i] for i in MIXIN)[:32]
    result = {str(k): re.sub(r"[!'()*]", '', str(v)) for k, v in
              sorted({**params, 'wts': int(time.time() if now is None else now)}.items())}
    result['w_rid'] = hashlib.md5((urlencode(result)+mix).encode()).hexdigest()
    return result


class ReadBudget:
    def __init__(self, requests=18, seconds=80, byte_limit=6*1024*1024, guard=None):
        self.limit, self.deadline, self.byte_limit = requests, time.monotonic()+seconds, byte_limit
        self.requests = self.bytes = 0
        self.guard = guard

    def claim(self):
        if self.guard:self.guard()
        if self.requests >= self.limit or time.monotonic() >= self.deadline:
            raise BiliError('task_budget', '本轮 B 站读取预算已用完')
        self.requests += 1


class BilibiliClient:
    def __init__(self, config, store, *, transport=None):
        self.config, self.store, self.db = config, store, store.db
        self.session_path = config.directory/'bilibili-session.enc'
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(12, connect=8), trust_env=False,
            follow_redirects=False, transport=transport,
            headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 WhalegirlReader/1.0',
                'Referer': 'https://www.bilibili.com/'})
        self.lock = asyncio.Lock()
        self.login_lock = asyncio.Lock()
        self.qr = None
        self.auth_generation = 0
        self.wbi = None
        self.wbi_at = 0
        self.last_request = 0
        for name in ('httpx', 'httpcore.http11', 'httpcore.http2'):
            logger = logging.getLogger(name)
            if not any(isinstance(f, PrivacyFilter) for f in logger.filters):
                logger.addFilter(PrivacyFilter())
        with self.store.lock, self.db:
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS bilibili_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bilibili_requests(id INTEGER PRIMARY KEY,created REAL NOT NULL,
                    operation TEXT NOT NULL,bytes INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS bilibili_request_time ON bilibili_requests(created);
            ''')

    def meta(self, key, default=''):
        row = self.db.execute('SELECT value FROM bilibili_meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, **values):
        with self.store.lock, self.db:
            self.db.executemany('INSERT OR REPLACE INTO bilibili_meta VALUES(?,?)',
                                [(key, str(value)) for key, value in values.items()])

    def cookies(self):
        if not self.session_path.exists():
            return {}
        try:
            if self.session_path.is_symlink():
                raise ValueError('redirected credential')
            value = json.loads(self.config.fernet.decrypt(self.session_path.read_bytes()))
            if not value:
                return {}
            if value.get('account') != self.config.settings.account_id:
                return {}
            return {key: val for key, val in value.get('cookies', {}).items() if key in COOKIE_NAMES
                    and isinstance(val, str) and 0 < len(val) <= 4096 and not re.search(r'[;\r\n]', val)}
        except Exception:
            self.set_meta(session_state='credential_error')
            raise BiliError('credential_error', 'B 站本地登录状态无法读取，请重新扫码') from None

    def save_cookies(self, values):
        if not values.get('SESSDATA'):
            raise BiliError('login_incomplete', '扫码已确认，但未取得有效登录状态，请重新扫码')
        clean = {key: value for key, value in values.items() if key in COOKIE_NAMES and isinstance(value, str)
                 and 0 < len(value) <= 4096 and not re.search(r'[;\r\n]', value)}
        if not clean.get('SESSDATA'):
            raise BiliError('login_incomplete', '登录会话内容无效，未保存')
        private_write(self.session_path, self.config.fernet.encrypt(json.dumps({
            'account': self.config.settings.account_id, 'cookies': clean}).encode()).decode())
        self.set_meta(session_state='unconfirmed', account_name='', last_check=0)
        self.wbi = None

    def pause(self, code):
        self.set_meta(paused='1', pause_code=code, pause_time=time.time())

    def resume(self):
        # Admin-only action; model tools never expose this method.
        self.set_meta(paused='0', pause_code='')

    def logout(self):
        if self.session_path.is_symlink():
            raise BiliError('credential_error', '登录状态路径异常，未执行退出')
        self.auth_generation += 1
        private_write(self.session_path, self.config.fernet.encrypt(b'{}').decode())
        self.http.cookies.clear()
        self.qr = self.wbi = None
        self.set_meta(session_state='anonymous', account_name='', last_check=0)
        # Logout cannot silently clear a platform risk pause.
        return self.status()

    def status(self):
        try:
            exists = bool(self.cookies())
        except BiliError:
            exists = False
        state = self.meta('session_state', 'anonymous')
        if not exists and state not in ('expired', 'credential_error'):
            state = 'anonymous'
        elif exists and state == 'anonymous':
            state = 'unconfirmed'
        return {'state': state, 'authenticated': state == 'logged_in' and exists,
            'account_name': self.meta('account_name') if exists else '',
            'last_check': float(self.meta('last_check', '0')),
            'paused': self.meta('paused') == '1', 'pause_code': self.meta('pause_code'),
            'qr_state': (self.qr or {}).get('state', ''),
            'qr_expires': (self.qr or {}).get('expires', 0), 'read_only': True,
            'requests_today': self.db.execute('SELECT count(*) FROM bilibili_requests WHERE created>?',
                (time.time()-24*3600,)).fetchone()[0], 'request_limit': self.config.settings.bilibili_requests_per_day}

    @staticmethod
    def permitted(raw, kind):
        url = urlsplit(raw)
        if url.scheme != 'https' or url.username or url.password or url.port not in (None, 443):
            return False
        if kind == 'api':
            return url.hostname == 'api.bilibili.com' and url.path in PATHS
        if kind == 'login':
            return url.hostname == 'passport.bilibili.com' and url.path in LOGIN_PATHS
        if kind == 'subtitle':
            return url.hostname in ('aisubtitle.hdslb.com', 'i0.hdslb.com', 'i1.hdslb.com') and (
                url.path.endswith('.json') or url.path.startswith('/bfs/ai_subtitle/'))
        if kind == 'frame':
            return ((url.hostname in ('i0.hdslb.com','i1.hdslb.com','i2.hdslb.com') and url.path.startswith('/bfs/videoshot/')) or
                    (url.hostname=='bimp.hdslb.com' and url.path.startswith('/videoshotpvhdboss/') and url.path.endswith(('.jpg','.png'))))
        if kind == 'frame_index':
            return url.hostname=='boss.hdslb.com' and url.path.startswith('/videoshotpvhdboss/') and url.path.endswith('.bin')
        if kind == 'media':
            return bool(url.hostname and (url.hostname.endswith('.bilivideo.com') or url.hostname.endswith('.bilivideo.cn'))
                        and url.path.startswith('/upgcxcode/'))
        return False

    async def get(self, raw, *, params=None, kind='api', budget=None, cap=1024*1024, json_body=True, cookie_only=False):
        if not self.permitted(raw, kind):
            raise BiliError('url_denied', 'B 站读取地址不在获准范围')
        if self.meta('paused') == '1':
            raise BiliError('risk_paused', 'B 站读取因风控暂停，请在后台确认后恢复')
        cookies = self.cookies() if kind == 'api' else {}
        if cookies and self.meta('session_state') in ('expired', 'credential_error'):
            raise BiliError('login_expired', 'B 站登录已失效，已停止认证读取，请重新扫码或主动切换匿名')
        async with self.lock:
            if budget:
                budget.claim()
            count = self.db.execute('SELECT count(*) FROM bilibili_requests WHERE created>?',
                                    (time.time()-24*3600,)).fetchone()[0]
            if count >= self.config.settings.bilibili_requests_per_day:
                raise BiliError('request_budget', 'B 站滚动24小时请求额度已用完')
            await asyncio.sleep(max(0, self.last_request+1-time.monotonic()))
            if budget and time.monotonic() >= budget.deadline:
                raise BiliError('task_budget', '本轮 B 站读取已超时')
            if budget and budget.guard:budget.guard()
            with self.store.lock, self.db:
                row = self.db.execute('INSERT INTO bilibili_requests(created,operation) VALUES(?,?)',
                                     (time.time(), kind))
                request_id = row.lastrowid
            headers = {'Cookie': '; '.join(key+'='+value for key, value in cookies.items())}
            remaining = max(.1, budget.deadline-time.monotonic()) if budget else 12
            observed=0
            try:
                async with self.http.stream('GET', raw, params=params, headers=headers, timeout=min(12, remaining)) as response:
                    if response.status_code in (401, 403, 412, 429):
                        if response.status_code == 401 and cookies:
                            self.set_meta(session_state='expired')
                        else:
                            self.pause('http_'+str(response.status_code))
                        raise BiliError('access_stopped', 'B 站拒绝本次读取，相关操作已停止，请查看账号状态')
                    crossdomain = kind == 'login' and urlsplit(raw).path == '/x/passport-login/web/crossDomain'
                    if response.status_code >= 300 and not (cookie_only and crossdomain and response.status_code in (301, 302)):
                        raise BiliError('http_error', 'B 站读取暂不可用（HTTP '+str(response.status_code)+'）')
                    if cookie_only:
                        if not crossdomain:
                            raise BiliError('url_denied', '仅允许登录确认接收会话')
                        return {}, response.cookies
                    if kind=='media':
                        try:declared=int(response.headers.get('content-length','0'))
                        except ValueError:declared=0
                        if declared>cap:raise BiliError('response_limit','媒体体积超过本次轻量读取上限，未下载整片')
                    raw_body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        observed+=len(chunk)
                        if budget:budget.bytes+=len(chunk)
                        raw_body.extend(chunk)
                        if budget and budget.bytes>budget.byte_limit:
                            raise BiliError('task_budget','本轮B站下载总预算已用完')
                        if len(raw_body) > cap:
                            raise BiliError('response_limit', '本轮 B 站材料超过读取大小上限')
                        if budget and time.monotonic() >= budget.deadline:
                            raise BiliError('task_budget', '本轮 B 站读取已超时')
                    with self.store.lock, self.db:
                        self.db.execute('UPDATE bilibili_requests SET bytes=? WHERE id=?', (len(raw_body), request_id))
                    if not json_body:
                        if raw_body.lstrip().startswith(b'{'):
                            try:code=json.loads(raw_body).get('code')
                            except (ValueError,AttributeError):code=None
                            if code in RISK_CODES:
                                self.pause('api_'+str(code))
                                raise BiliError('risk_paused','B站触发访问限制，已暂停读取')
                        return bytes(raw_body)
                    value = json.loads(raw_body)
                    if not isinstance(value, dict):
                        raise BiliError('invalid_response', 'B 站返回格式未确认')
                    code = value.get('code', 0)
                    if not isinstance(code,int) or isinstance(code,bool):
                        raise BiliError('invalid_response','B站返回状态格式未确认')
                    if code in RISK_CODES:
                        self.pause('api_'+str(code))
                        raise BiliError('risk_paused', 'B 站触发访问限制，已持久暂停读取')
                    if code in LOGIN_CODES and not (urlsplit(raw).path == '/x/web-interface/nav' and not cookies):
                        if cookies:
                            self.set_meta(session_state='expired')
                            raise BiliError('login_expired', 'B 站登录失效，认证读取已停止')
                        raise BiliError('login_required','本次材料需要登录，未尝试绕过')
                    if code not in (0, -101):
                        raise BiliError('api_unavailable', 'B 站本次未提供可用材料（代码 '+str(code)+')')
                    return value, response.cookies
            except BiliError:
                raise
            except (httpx.HTTPError, ValueError, UnicodeError):
                raise BiliError('network_error', 'B 站读取中断，未自动重试') from None
            finally:
                with self.store.lock,self.db:
                    self.db.execute('UPDATE bilibili_requests SET bytes=? WHERE id=?',(observed,request_id))
                self.http.cookies.clear()
                self.last_request = time.monotonic()

    async def api(self, path, params=None, *, budget=None, wbi=False):
        if wbi:
            if not self.wbi or time.time()-self.wbi_at > 6*3600:
                await self.check_login(budget=budget)
            params = signed(params or {}, self.wbi or {})
        value, _ = await self.get(API+path, params=params, budget=budget)
        data=value.get('data') or {}
        if not isinstance(data,dict):raise BiliError('invalid_response','B站材料结构未确认')
        return data

    async def check_login(self, *, budget=None):
        value, _ = await self.get(API+'/x/web-interface/nav', budget=budget)
        data = value.get('data') or {}
        self.wbi, self.wbi_at = data.get('wbi_img'), time.time()
        has_cookies = bool(self.cookies())
        self.set_meta(session_state='logged_in' if data.get('isLogin') else 'expired' if has_cookies else 'anonymous',
                      account_name=str(data.get('uname') or '')[:80] if has_cookies else '', last_check=time.time())
        if has_cookies and not data.get('isLogin'):
            raise BiliError('login_expired', 'B 站登录失效，请重新扫码')
        return self.status()

    async def start_login(self):
        async with self.login_lock:
            self.auth_generation += 1
            generation=self.auth_generation
            account=self.config.settings.account_id
            value, _ = await self.get(PASSPORT+'/x/passport-login/web/qrcode/generate', kind='login')
            if generation!=self.auth_generation or account!=self.config.settings.account_id:
                raise BiliError('login_cancelled','登录流程已取消')
            data = value.get('data') or {}
            key, url = data.get('qrcode_key'), data.get('url')
            parsed = urlsplit(url or '')
            landing=(parsed.hostname,parsed.path)
            if not isinstance(key, str) or not re.fullmatch('[A-Za-z0-9_-]{16,200}', key) or (
                    parsed.scheme != 'https' or landing not in {
                        ('passport.bilibili.com','/h5-app/passport/login/scan'),
                        ('account.bilibili.com','/h5/account-h5/auth/scan-web')}
                    or parsed.username or parsed.password or parsed.port not in (None,443)):
                raise BiliError('login_unavailable', 'B 站未返回可用扫码地址')
            self.qr = {'key': key, 'url': url, 'state': 'waiting_scan', 'expires': time.time()+170,
                       'polled': 0,'account':account,'generation':generation}
            return self.status()

    def qr_png(self):
        if not self.qr or time.time() >= self.qr['expires']:
            raise BiliError('qr_expired', 'B 站二维码已过期，请刷新')
        import qrcode
        output = io.BytesIO()
        qrcode.make(self.qr['url']).save(output, format='PNG')
        return output.getvalue()

    async def poll_login(self):
        async with self.login_lock:
            if not self.qr or time.time() >= self.qr['expires']:
                if self.qr:
                    self.qr['state'] = 'expired'
                return self.status()
            if self.qr['state'] == 'logged_in' or time.time()-self.qr['polled'] < 3:
                return self.status()
            self.qr['polled'] = time.time()
            qr=self.qr
            value, response_cookies = await self.get(PASSPORT+'/x/passport-login/web/qrcode/poll',
                kind='login', params={'qrcode_key': self.qr['key']})
            if self.qr is not qr or qr['generation']!=self.auth_generation or qr['account']!=self.config.settings.account_id:
                raise BiliError('login_cancelled','登录流程已取消')
            data = value.get('data') or {}
            code = data.get('code')
            if code in (86101, 86090, 86038):
                self.qr['state'] = {86101: 'waiting_scan', 86090: 'waiting_confirm', 86038: 'expired'}[code]
                return self.status()
            if code != 0:
                raise BiliError('login_unavailable', 'B 站扫码状态暂未确认')
            cookies = {cookie.name: cookie.value for cookie in response_cookies.jar if cookie.name in COOKIE_NAMES}
            credential_url = str(data.get('url') or '')
            if credential_url and not cookies.get('SESSDATA'):
                if not self.permitted(credential_url.split('?', 1)[0], 'login'):
                    raise BiliError('url_denied', '登录确认地址不在 B 站认证范围')
                query = parse_qs(urlsplit(credential_url).query)
                cookies.update({key: values[0] for key, values in query.items() if key in COOKIE_NAMES and values})
                if not cookies.get('SESSDATA'):
                    _, confirmed_cookies = await self.get(credential_url, kind='login', cookie_only=True)
                    cookies.update({cookie.name: cookie.value for cookie in confirmed_cookies.jar if cookie.name in COOKIE_NAMES})
            if self.qr is not qr or qr['generation']!=self.auth_generation or qr['account']!=self.config.settings.account_id:
                raise BiliError('login_cancelled','登录流程已取消')
            self.save_cookies(cookies)
            await self.check_login()
            self.qr['state'] = 'logged_in'
            return self.status()

    async def close(self):
        await self.http.aclose()
