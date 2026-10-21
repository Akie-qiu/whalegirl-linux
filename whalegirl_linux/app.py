from __future__ import annotations

import hmac
import asyncio
import time
import os
import httpx
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .gateway import AgentGateway
from .runtime import Runtime
from .settings import Config, Settings
from .store import Store
from .stability import read_stability


class ConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: Settings
    api_key: str = Field(default="", max_length=1024)


class ReviewRequest(BaseModel):
    key: str
    received: bool


class PromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system_prompt: str = Field(default="", max_length=16000)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    password: str = Field(min_length=1,max_length=1024)


class MemoryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    op: str = Field(default='create',pattern='^(create|update|archive|restore)$')
    id: int | None = None
    version: int | None = None
    chat: str | None = Field(default=None,max_length=200)
    subject: str | None = Field(default=None,max_length=200)
    kind: str | None = Field(default=None,pattern='^(fact|preference|impression|relationship|event)$')
    scope: str | None = Field(default=None,pattern='^(chat|profile)$')
    content: str | None = Field(default=None,max_length=700)
    source_key: str = Field(default='',max_length=700)


class AffectionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    subject: str = Field(min_length=1,max_length=200)
    version: int = Field(ge=0)
    score: int | None = Field(default=None,ge=0,le=100)
    locked: bool | None = None
    reason: str = Field(default='管理员调整',max_length=120)


class StickerLibraryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern='^[0-9a-f]{32}$')
    op: str = Field(pattern='^(enable|disable|pin|unpin|retry)$')
    version: int | None = Field(default=None,ge=0)
    reason: str = Field(default='管理员调整',max_length=180)


def create_app(directory=None, *, gateway=None, engine_factory=None, run_background=True):
    root = Path(__file__).resolve().parent.parent
    data = Path(directory or os.environ.get("WHALEGIRL_DATA_DIR", root / "data"))
    config = Config(data)
    store = Store(data / "state.sqlite3")
    transport = gateway or AgentGateway(os.environ.get("WHALEGIRL_GATEWAY_URL", "http://127.0.0.1:6175"), config.gateway_token, control_directory=data)
    kwargs = {"engine_factory": engine_factory} if engine_factory else {}
    runtime = Runtime(config, store, transport, root / "styles", **kwargs)

    @asynccontextmanager
    async def lifespan(app):
        if run_background:
            await runtime.start()
        yield
        await runtime.close()

    app = FastAPI(title="鲸鱼娘 Linux 轻量版", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.runtime = runtime
    app.state.config = config
    auth_attempts = {}
    public_host = os.environ.get('WHALEGIRL_PUBLIC_HOST', '').strip().lower()
    public_origins = {value.strip().rstrip('/') for value in
                      os.environ.get('WHALEGIRL_PUBLIC_ORIGINS', '').split(',') if value.strip()}

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        host = request.headers.get("host", "").split(":")[0].lower()
        origin = request.headers.get("origin")
        allowed_hosts = {"127.0.0.1", "localhost", "testserver"}
        if public_host:
            allowed_hosts.add(public_host)
        allowed_hosts.update(urlsplit(value).hostname for value in public_origins if urlsplit(value).hostname)
        allowed_origins = {"http://127.0.0.1", "http://localhost"}
        if public_host:
            allowed_origins.add("https://" + public_host)
        allowed_origins.update(public_origins)
        if host.endswith('.trycloudflare.com') and host.count('.') >= 2:
            allowed_hosts.add(host)
            allowed_origins.add('https://' + host)
        if host not in allowed_hosts or (origin and origin.rstrip('/') not in allowed_origins):
            return JSONResponse({"detail": "此访问地址未授权"}, 403)
        if request.url.path.startswith("/api/") and request.url.path!='/api/auth/login':
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
            if not config.authorize_admin(supplied):
                return JSONResponse({"detail": "请先使用控制台密码登录"}, 401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0", "wechat_version": config.settings.expected_wechat_version}

    @app.get("/")
    async def index():
        return FileResponse(root / "whalegirl_linux" / "web.html")

    @app.post('/api/auth/login')
    async def console_login(body: LoginRequest, request: Request):
        peer = request.client.host if request.client else 'local'
        now = time.time()
        attempts = [t for t in auth_attempts.get(peer,[]) if t>now-60]
        if len(attempts)>=5:
            raise HTTPException(429,'控制台登录请求过多，请一分钟后再试')
        auth_attempts[peer] = attempts+[now]
        session = await asyncio.to_thread(config.console_login,body.password)
        if not session:
            raise HTTPException(401,'控制台密码不正确')
        return {'token':session,'expires_in':86400}

    @app.post('/api/auth/logout')
    async def console_logout(request: Request):
        supplied = request.headers.get('authorization','').removeprefix('Bearer ')
        config.console_logout(supplied)
        return {'ok':True}

    @app.get("/console.js")
    async def console_script():
        return FileResponse(root / "whalegirl_linux" / "console.js", media_type="text/javascript")

    @app.get("/console.css")
    async def console_style():
        return FileResponse(root / "whalegirl_linux" / "console.css", media_type="text/css")

    @app.get('/habits.js')
    async def habits_script():
        return FileResponse(root/'whalegirl_linux'/'habits.js',media_type='text/javascript')

    @app.get('/improvements.js')
    async def improvements_js():
        return FileResponse(root/'whalegirl_linux'/'improvements.js',media_type='text/javascript')

    @app.get('/affection.js')
    async def affection_js():
        return FileResponse(root/'whalegirl_linux'/'affection.js',media_type='text/javascript')

    @app.get('/sticker-library.js')
    async def sticker_library_js():
        return FileResponse(root/'whalegirl_linux'/'sticker-library.js',media_type='text/javascript')

    @app.get("/api/state")
    async def state():
        return runtime.snapshot()

    @app.get('/api/stability')
    def stability():
        return read_stability(data)

    @app.get('/api/stability/export')
    def stability_export():
        return JSONResponse(read_stability(data, export=True), headers={
            'Content-Disposition': 'attachment; filename="whalegirl-runtime-log.json"'})

    @app.get("/api/config")
    async def get_config():
        return config.public()

    @app.post("/api/config")
    async def save_config(body: ConfigRequest):
        settings = body.settings
        if "trigger_only_chat_ids" not in settings.model_fields_set:
            settings.trigger_only_chat_ids = config.settings.trigger_only_chat_ids
        if "system_prompt_override" not in settings.model_fields_set:
            settings.system_prompt_override = config.settings.system_prompt_override
        if "quote_replies_enabled" not in settings.model_fields_set:
            settings.quote_replies_enabled = config.settings.quote_replies_enabled
        if "sticker_aggressiveness" not in settings.model_fields_set:
            settings.sticker_aggressiveness = config.settings.sticker_aggressiveness
        for name in Settings.model_fields:
            if name not in settings.model_fields_set:
                setattr(settings,name,getattr(config.settings,name))
        if settings.enabled:
            try:
                status = await transport.status()
            except (httpx.HTTPError, ValueError):
                status = {"status": "disconnected"}
            if status.get("status") == "logged_in" and status.get("loggedInUser"):
                settings.account_id = status["loggedInUser"]
            elif not settings.account_id or settings.account_id != config.settings.account_id:
                raise HTTPException(409, "先登录微信，再选择允许回复的会话")
        try:
            config.save(settings, body.api_key)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not store.stats().get("uncertain"):
            runtime.paused_reason = ""
        return config.public()

    def prompt_state():
        default = runtime.styles.get_active().system_prompt
        override = config.settings.system_prompt_override
        return {"default": default, "effective": override.strip() or default,
                "customized": bool(override.strip()), "max_length": 16000}

    @app.get("/api/prompt")
    async def get_prompt():
        return prompt_state()

    @app.post("/api/prompt")
    async def save_prompt(body: PromptRequest):
        config.save(config.settings.model_copy(update={"system_prompt_override": body.system_prompt.strip()}))
        return prompt_state()

    @app.post("/api/model/test")
    async def test_model():
        if not config.api_key():
            raise HTTPException(400, "先保存 API Key")
        engine = await runtime.model()
        text = await engine.chat([{"role": "user", "content": "只回复：连接正常"}], max_tokens=32)
        return {"ok": bool(text), "message": text[:100] if text else engine.last_error or "模型未返回内容"}

    @app.post("/api/gateway/login")
    async def login():
        status = await transport.status()
        if status.get("status") == "logged_in" and status.get("loggedInUser"):
            return transport.login
        if not runtime.safety.login_request(config.settings.account_id,config.settings):
            raise HTTPException(429,'本地登录保护：10分钟内最多请求3次登录，请稍后再试')
        return await transport.start_login()

    @app.get('/api/safety')
    async def get_safety():
        return runtime.safety.snapshot(config.settings.account_id,config.settings)

    @app.post('/api/safety/check')
    async def check_safety():
        return await runtime.check_safety()

    @app.post('/api/safety/resume')
    async def resume_safety():
        result = await runtime.check_safety()
        auth = runtime.gateway_state
        if store.stats().get('uncertain'):
            raise HTTPException(409,'仍有发送结果不确定的记录，请先在处理记录中核对')
        if result['check_error']:
            raise HTTPException(503,'暂时无法检查微信客户端，请稍后再恢复')
        if auth.get('status')!='logged_in' or auth.get('loggedInUser')!=config.settings.account_id:
            raise HTTPException(409,'请先登录授权微信账号，再恢复发送')
        if result['client_warning']:
            raise HTTPException(409,'微信客户端仍有异常提示，请先在微信中处理，当前不恢复发送')
        runtime.safety.resume(config.settings.account_id)
        runtime.paused_reason = ''
        return runtime.safety.snapshot(config.settings.account_id,config.settings)

    @app.get("/api/gateway/chats")
    async def chats():
        try:
            return await runtime.refresh_chats()
        except Exception as exc:
            raise HTTPException(502, f"获取会话失败：{type(exc).__name__}") from exc

    @app.get("/api/recent")
    async def recent():
        return store.recent()

    @app.get('/api/sticker-library')
    def sticker_library_state():
        s=config.settings
        with store.lock:return runtime.sticker_library.inventory(s.account_id,s.allowed_chat_ids)

    @app.post('/api/sticker-library/manage')
    def manage_sticker(body: StickerLibraryRequest):
        s=config.settings
        try:
            if body.op=='retry':
                runtime.sticker_library.retry(s.account_id,body.id,s.allowed_chat_ids)
                return {'ok':True}
            return runtime.sticker_library.change(s.account_id,body.id,body.op,body.reason,version=body.version)
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc

    @app.post('/api/sticker-library/revisions/{revision_id}/undo')
    def undo_sticker(revision_id: int):
        try:return runtime.sticker_library.undo(config.settings.account_id,revision_id)
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc

    @app.get('/api/stickers')
    async def stickers():
        return {'stickers':runtime.stickers.choices(),'profiles':runtime.stickers.profiles,'last_refresh':runtime.stickers.refreshed,'error':runtime.stickers.error}

    @app.post('/api/stickers/{md5}')
    def edit_sticker(md5: str, body: dict):
        try:return runtime.stickers.edit(md5,body)
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc

    @app.get('/api/messages')
    def received_messages(chat: str='',q: str='',before_time: float | None=None,before_id: int=0,before_key: str='',limit: int=50):
        s=config.settings
        if chat and chat not in s.allowed_chat_ids:raise HTTPException(403,'该会话未授权')
        result=store.received(s.account_id,s.allowed_chat_ids,chat,q,before_time,before_id,before_key,max(1,min(100,limit)))
        result['reading']=[dict(r) for r in store.db.execute('SELECT chat,last_read,completed,error FROM archive_progress WHERE account=?',(s.account_id,)) if r['chat'] in s.allowed_chat_ids]
        return result

    @app.get('/api/messages/image')
    def received_image(key: str):
        s=config.settings
        row=store.db.execute('SELECT chat FROM message_log WHERE key=? AND account=?',(key,s.account_id)).fetchone()
        if not row or row['chat'] not in s.allowed_chat_ids:raise HTTPException(404,'图片消息不存在或会话未授权')
        media=store.cached_image(key)
        if not media.get('data'):raise HTTPException(404,'原图尚未缓存')
        return {'format':media.get('format','jpeg'),'quality':media.get('quality',''),'data':media['data']}

    @app.get('/api/memory/export')
    def export_memories(chat: str=''):
        s=config.settings
        if not chat or chat not in s.allowed_chat_ids:raise HTTPException(400,'请选择一个已授权会话')
        people={r[0] for r in store.db.execute('SELECT DISTINCT sender_id FROM message_log WHERE account=? AND chat=?',(s.account_id,chat))}
        marks=','.join('?' for _ in people)
        if people and s.global_memory and s.memory_scope_mode=='shared_profiles':
            rows=store.db.execute(f'''SELECT * FROM memories WHERE account=? AND chat IN ({','.join('?' for _ in s.allowed_chat_ids)})
                AND (chat=? AND scope='chat' OR scope='profile' AND subject IN ({marks})) ORDER BY id''',
                (s.account_id,*s.allowed_chat_ids,chat,*people)).fetchall()
        else:
            rows=store.db.execute("SELECT * FROM memories WHERE account=? AND chat=? ORDER BY id",(s.account_id,chat)).fetchall()
        return JSONResponse({'chat':chat,'exported_at':time.time(),'memories':[dict(r) for r in rows]},
                            headers={'Content-Disposition':'attachment; filename="wechat-memories.json"'})

    @app.get('/api/affection')
    def affection_people(q: str=''):
        s=config.settings
        with store.lock:
            return {'people':runtime.affection.people(s.account_id,s.allowed_chat_ids,s.affection_initial_score,q[:200]),
                    'enabled':s.affection_enabled,'scope':'shared_person'}

    @app.get('/api/affection/revisions')
    def affection_revisions(subject: str):
        s=config.settings
        with store.lock:
            return {'revisions':runtime.affection.revisions(s.account_id,subject,s.allowed_chat_ids)}

    @app.post('/api/affection')
    def change_affection(body: AffectionRequest):
        s=config.settings
        try:
            return runtime.affection.change(s.account_id,s.allowed_chat_ids,initial=s.affection_initial_score,
                                           **body.model_dump())
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc

    @app.post('/api/affection/revisions/{revision_id}/undo')
    def undo_affection(revision_id: int):
        s=config.settings
        try:
            return runtime.affection.undo(s.account_id,s.allowed_chat_ids,revision_id)
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc

    @app.get('/api/memory')
    def memories(q: str='', include_archived: bool=False, chat: str=''):
        s=config.settings
        if chat and chat not in s.allowed_chat_ids:raise HTTPException(403,'该会话未授权')
        people={r[0] for r in store.db.execute('SELECT DISTINCT sender_id FROM message_log WHERE account=? AND chat=?',(s.account_id,chat))} if chat else set()
        rows=runtime.memory_book.search(s.account_id,s.allowed_chat_ids,q[:200],archived=include_archived,limit=100,
            chat=chat or None,subjects=people,shared=s.global_memory and s.memory_scope_mode=='shared_profiles')
        return {'memories':rows,
                'revisions':runtime.memory_book.revisions(s.account_id,s.allowed_chat_ids),'ai_editing':s.editable_memory_enabled}

    @app.get('/api/memory/subjects')
    def memory_subjects():
        s=config.settings
        if not s.allowed_chat_ids:return []
        marks=','.join('?' for _ in s.allowed_chat_ids)
        rows=store.db.execute(f'''SELECT sender_id AS id,max(sender) AS name FROM message_log
            WHERE account=? AND chat IN ({marks}) AND is_self=0 AND sender_id!=''
            GROUP BY sender_id ORDER BY max(timestamp) DESC LIMIT 300''',(s.account_id,*s.allowed_chat_ids)).fetchall()
        return [dict(r) for r in rows]

    @app.post('/api/memory')
    def memory_change(body: MemoryRequest):
        s=config.settings
        try:return runtime.memory_book.change(s.account_id,s.allowed_chat_ids,body.model_dump(exclude_none=True),actor='admin')
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc

    @app.post('/api/memory/revisions/{revision_id}/undo')
    def memory_undo(revision_id: int):
        s=config.settings
        try:return runtime.memory_book.undo(s.account_id,s.allowed_chat_ids,revision_id)
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc

    @app.post('/api/stickers/refresh')
    async def refresh_stickers():
        await runtime.stickers.refresh(transport,await runtime.model())
        return {'available':len(runtime.stickers.choices()),'error':runtime.stickers.error}

    @app.post("/api/review")
    async def review(body: ReviewRequest):
        event = store.get(body.key)
        if not event or event["state"] != "uncertain":
            raise HTTPException(409, "这条记录不需要核对")
        actions=store.actions(body.key)
        for item in actions:
            if item['state']=='uncertain':
                store.action_finish(item['id'],'sent' if body.received else 'failed',
                                    'manual_confirmation' if body.received else '',error='' if body.received else '人工确认未发送')
        confirmed=[r['reply'] for r in store.actions(body.key) if r['state']=='sent']
        store.update(body.key, "sent" if body.received or confirmed else "failed", reply='\n'.join(confirmed) if actions else event['reply'],
                     receipt="manual_confirmation" if body.received else "", error="人工核对：未发送" if not body.received else "")
        if body.received and not actions:
            store.remember(event, event["reply"])
        if not store.stats().get("uncertain"):
            runtime.paused_reason = ""
        return {"ok": True, "automatic_resend": False}

    return app
