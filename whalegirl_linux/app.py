from __future__ import annotations

import hmac
import base64
import io
import json
import re
import asyncio
import time
import os
import httpx
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .gateway import AgentGateway
from .runtime import Runtime
from .settings import Config, Settings
from .store import Store
from .stability import read_stability
from .personas import PersonaLibrary, PersonaSpec, PersonaSaveRequest, PersonaActionRequest, compile_persona
from .conversation import admin_persona_state_action


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

class TiebaConfigRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    values: dict = Field(default_factory=dict)
    bduss: str = Field(default='', max_length=4096)
    stoken: str = Field(default='', max_length=4096)

class TiebaEditRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    phrase: str | None = Field(default=None, min_length=1, max_length=80)
    meaning: str | None = Field(default=None, max_length=400)
    scene: str | None = Field(default=None, max_length=240)
    background: str | None = Field(default=None, max_length=800)
    examples: str | None = Field(default=None, max_length=600)
    avoid: str | None = Field(default=None, max_length=400)
    aliases: str | None = Field(default=None, max_length=300)
    enabled: bool | None = None
    renew: bool = False
    op: str | None = Field(default=None, pattern='^(cancel|received|not_received|retry)$')


class TiebaLifecycleRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    op: str = Field(pattern='^(pin|unpin|restore)$')


class ProactiveConfigRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    values: dict = Field(default_factory=dict)


class ProactivePauseRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    chat: str = Field(min_length=1, max_length=200)
    resume: bool = False


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
    kind: str | None = Field(default=None,pattern='^(fact|preference|impression|relationship|event|joke|self_stance|shared_moment|chat_style|meme_usage|tool_learning|expression|persona|bot_alias|slang|nickname|interaction_rule)$')
    scope: str | None = Field(default=None,pattern='^(chat|profile)$')
    content: str | None = Field(default=None,max_length=700)
    source_key: str | None = Field(default=None,max_length=700)
    ttl_days: int | None = Field(default=None,ge=1,le=90)
    triggers: str | list[str] | None = None
    salience: int | None = Field(default=None,ge=1,le=5)
    tier: str | None = Field(default=None,pattern='^(core|stable|ordinary|cold)$')
    status: str | None = Field(default=None,pattern='^(active|pending|superseded|forgotten|expired|rejected|blocked)$')
    locked: bool | None = None
    venue: str | None = Field(default=None,pattern='^(any|group|private)$')
    property_key: str | None = Field(default=None,max_length=80)
    derived_from: list[int] | None = Field(default=None,max_length=8)


class AffectionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    subject: str = Field(min_length=1,max_length=200)
    version: int = Field(ge=0)
    score: int | None = Field(default=None,ge=0,le=100)
    locked: bool | None = None
    reason: str = Field(default='管理员调整',max_length=120)
    nickname: str | None = Field(default=None,max_length=40)
    vibe_tags: list[str] | str | None = None
    interaction_rule: str | None = Field(default=None,max_length=200)
    notable_events: str | None = Field(default=None,max_length=300)
    profile_locked: bool | None = None


class LearningRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    op: str = Field(pattern='^(accept|reject|revoke|revise|block)$')
    version: int = Field(ge=1)
    content: str | None = Field(default=None, min_length=1, max_length=400)
    ttl_days: int | None = Field(default=None, ge=1, le=365)
    reason: str | None = Field(default=None, max_length=120)


class PersonaStateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    op: str = Field(pattern='^(reset_mood|refresh_life_scene|reset_group_mask|save_group_mask)$')
    chat: str = Field(default='', max_length=200)
    mood: str | None = Field(default=None, max_length=30)
    energy: int | None = Field(default=None, ge=10, le=100)
    life_override: str | None = Field(default=None, max_length=120)
    teasing_delta: int | None = Field(default=None, ge=-1, le=1)
    banter_delta: int | None = Field(default=None, ge=-1, le=1)
    guard_level: str | None = Field(default=None, max_length=20)
    vibe_summary: str | None = Field(default=None, max_length=160)
    locked: bool | None = None


class StickerLibraryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern='^[0-9a-f]{32}$')
    op: str = Field(pattern='^(enable|disable|pin|unpin|retry|skip)$')
    chat: str = Field(default='',max_length=200)
    version: int | None = Field(default=None,ge=0)
    reason: str = Field(default='管理员调整',max_length=180)


class StickerBatchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ids: list[str] = Field(min_length=1,max_length=40)
    op: str = Field(pattern='^(retry|skip)$')


class MomentsDraftRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: str = Field(default='publish', pattern='^(publish|reply_own|comment_friend)$')
    post_id: str = Field(default='', max_length=30)
    comment_id: str = Field(default='', max_length=30)


class MomentsActionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    op: str = Field(pattern='^(edit|publish|cancel|received|not_received|retry|without_image)$')
    text: str = Field(default='', max_length=180)
    assets: list[str] | None = Field(default=None, max_length=9)


class MomentsAssetRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    data: str = Field(min_length=1, max_length=8_000_000)
    name: str = Field(default='朋友圈配图', max_length=100)


class ImageConfigRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    values: dict = Field(default_factory=dict)
    api_key: str = Field(default='', max_length=1024)


class ImageTaskRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: str = Field(default='generate', pattern='^(generate|send)$')
    prompt: str = Field(default='', max_length=3000)
    asset_id: str = Field(default='', max_length=64)
    destination: str = Field(default='library', pattern='^(library|chat|moments)$')
    chat: str = Field(default='', max_length=200)
    moment_id: str = Field(default='', max_length=64)
    reference_ids: list[str] = Field(default_factory=list, max_length=8)
    character: bool = False
    character_variant: str = Field(default='', pattern='^(|chibi|girl|mature)$')
    mode: str = Field(default='auto', pattern='^(auto|edit|remix)$')


class ReferenceUploadRequest(MomentsAssetRequest):
    variant: str = Field(pattern='^(chibi|girl|mature|temporary)$')
    role: str = Field(default='', max_length=400)


class ReferenceEditRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    variant: str = Field(pattern='^(chibi|girl|mature)$')
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(default='', max_length=400)
    enabled: bool = True


class ImageEditRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default='', max_length=600)
    scenes: str = Field(default='', max_length=300)
    enabled: bool = True
    scope: str = Field(default='admin', pattern='^(admin|global|chat)$')
    scope_chat: str = Field(default='', max_length=200)
    character_id: str = Field(default='', pattern='^(|whalegirl)$')
    character_variant: str = Field(default='', pattern='^(|chibi|girl|mature)$')
    retention_exempt: bool = False


class ImageDeleteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm_id: str = Field(pattern='^[0-9a-f]{64}$')


class ImageActionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    op: str = Field(pattern='^(cancel|received|not_received|retry_metadata)$')


def create_app(directory=None, *, gateway=None, engine_factory=None, run_background=True):
    root = Path(__file__).resolve().parent.parent
    data = Path(directory or os.environ.get("WHALEGIRL_DATA_DIR", root / "data"))
    config = Config(data)
    store = Store(data / "state.sqlite3")
    transport = gateway or AgentGateway(os.environ.get("WHALEGIRL_GATEWAY_URL", "http://127.0.0.1:6175"), config.gateway_token, control_directory=data)
    kwargs = {"engine_factory": engine_factory} if engine_factory else {}
    runtime = Runtime(config, store, transport, root / "styles", **kwargs)
    personas = PersonaLibrary(config, runtime.styles.get_active().system_prompt)

    @asynccontextmanager
    async def lifespan(app):
        if run_background:
            await runtime.start()
        yield
        await runtime.close()

    app = FastAPI(title="鲸鱼娘 Linux 轻量版", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.runtime = runtime
    app.state.config = config
    app.state.personas = personas
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
        allowed_origins = {"http://127.0.0.1", "http://localhost",
                           "http://127.0.0.1:18092", "http://localhost:18092"}
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
        failed_tasks = sum(task.done() for task in runtime.tasks)
        return {"status": "degraded" if failed_tasks else "ok",
                "background_tasks": len(runtime.tasks),
                "failed_background_tasks": failed_tasks,
                "version": "0.1.0",
                "wechat_version": config.settings.expected_wechat_version}

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

    @app.get('/token-usage.js')
    async def token_usage_script():
        return FileResponse(root/'whalegirl_linux'/'token-usage.js',media_type='text/javascript')

    @app.get('/api/token-usage')
    async def token_usage(period: str='today'):
        if period not in ('today','7d','30d','all'):raise HTTPException(400,'统计范围无效')
        return runtime.token_ledger.snapshot(config.settings.account_id,period)

    @app.get('/personas.js')
    async def personas_script():
        return FileResponse(root/'whalegirl_linux'/'personas.js', media_type='text/javascript')

    @app.get('/personas.css')
    async def personas_css():
        return FileResponse(root/'whalegirl_linux'/'personas.css', media_type='text/css')

    @app.get('/api/personas')
    async def personas_state():
        try:return personas.snapshot()
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc

    @app.post('/api/personas/save')
    async def personas_save(body: PersonaSaveRequest):
        try:return personas.save(body)
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc

    @app.post('/api/personas/preview')
    async def personas_preview(body: PersonaSpec):
        try:
            prompt=compile_persona(body)
            return {'prompt':prompt,'length':len(prompt),'max_length':16000}
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc

    @app.post('/api/personas/{identity}/actions')
    async def personas_action(identity: str, body: PersonaActionRequest):
        try:return personas.action(identity,body)
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc

    @app.get('/habits.js')
    async def habits_script():
        return FileResponse(root/'whalegirl_linux'/'habits.js',media_type='text/javascript')

    @app.get('/learning.js')
    async def learning_script():
        return FileResponse(root/'whalegirl_linux'/'learning.js', media_type='text/javascript')

    @app.get('/memory-hub.js')
    async def memory_hub_script():
        return FileResponse(root/'whalegirl_linux'/'memory-hub.js',media_type='text/javascript')

    @app.get('/improvements.js')
    async def improvements_js():
        return FileResponse(root/'whalegirl_linux'/'improvements.js',media_type='text/javascript')

    @app.get('/affection.js')
    async def affection_js():
        return FileResponse(root/'whalegirl_linux'/'affection.js',media_type='text/javascript')

    @app.get('/sticker-library.js')
    async def sticker_library_js():
        return FileResponse(root/'whalegirl_linux'/'sticker-library.js',media_type='text/javascript')

    @app.get('/sticker-library.css')
    async def sticker_library_css():
        return FileResponse(root/'whalegirl_linux'/'sticker-library.css',media_type='text/css')

    @app.get('/moments.js')
    async def moments_script():
        return FileResponse(root/'whalegirl_linux'/'moments.js', media_type='text/javascript')

    @app.get('/images.js')
    async def images_script():
        return FileResponse(root/'whalegirl_linux'/'images.js', media_type='text/javascript')

    @app.get('/tieba.js')
    async def tieba_script():
        return FileResponse(root/'whalegirl_linux'/'tieba.js', media_type='text/javascript')

    @app.get('/api/tieba')
    async def tieba_state():
        return runtime.tieba.snapshot()

    @app.get('/proactive.js')
    async def proactive_script():
        return FileResponse(root/'whalegirl_linux'/'proactive.js', media_type='text/javascript')

    @app.get('/api/proactive')
    async def proactive_state():
        return runtime.proactive.snapshot()

    @app.post('/api/proactive/config')
    async def proactive_config(body: ProactiveConfigRequest):
        allowed = {name for name in Settings.model_fields if name.startswith('proactive_')}
        if set(body.values) - allowed:
            raise HTTPException(400, '自主聊天设置包含未知参数')
        try:
            updated = Settings.model_validate({**config.settings.model_dump(), **body.values})
            if set(updated.proactive_chat_ids) - set(updated.allowed_chat_ids):
                raise ValueError('主动聊天对象必须先在会话设置中授权')
            if set(updated.proactive_strict_chat_ids) - set(updated.proactive_chat_ids):
                raise ValueError('严格群主动权限必须属于已选择的主动聊天会话')
            config.save(updated)
        except ValueError as exc:
            raise HTTPException(400, '请检查参数范围和会话授权：'+str(exc)[:160])
        return config.public()

    @app.post('/api/proactive/pause')
    async def proactive_pause(body: ProactivePauseRequest):
        try:
            runtime.proactive.pause(body.chat, body.resume)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return runtime.proactive.snapshot()

    @app.post('/api/tieba/config')
    async def tieba_config(body: TiebaConfigRequest):
        allowed = {name for name in Settings.model_fields if name.startswith('tieba_')}
        if set(body.values) - allowed:
            raise HTTPException(400, '贴吧设置包含未知参数')
        try:
            updated = Settings.model_validate({**config.settings.model_dump(), **body.values})
            if set(updated.tieba_share_chat_ids) - set(updated.allowed_chat_ids):
                raise ValueError('分享会话必须已在会话设置里授权')
            config.save(updated)
            if not updated.tieba_enabled:
                runtime.tieba.requested = False
            config.save_tieba_credentials(body.bduss, body.stoken)
            return config.public()
        except ValueError:
            raise HTTPException(400, '请检查贴吧参数范围及分享会话；登录凭据请只填写对应字段')

    @app.post('/api/tieba/scan')
    async def tieba_scan():
        try:
            return runtime.tieba.request_scan()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get('/api/tieba/archives')
    async def tieba_archives(offset: int = 0, limit: int = 20):
        return runtime.tieba.archives.summaries(offset, limit)

    @app.get('/api/tieba/search')
    async def tieba_search(query: str = '', limit: int = 5):
        return runtime.tieba.tools.search(query[:500], limit)

    @app.get('/api/tieba/tools/read')
    async def tieba_read_source(tid: str, pid: str = '', offset: int = 0):
        return runtime.tieba.tools.read(tid, pid, offset)

    @app.post('/api/tieba/lifecycle/{tid}')
    async def tieba_lifecycle(tid: str, body: TiebaLifecycleRequest):
        try:
            return runtime.tieba.retention.edit(tid, body.op)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post('/api/tieba/posts/{tid}/archive')
    async def tieba_archive_post(tid: str):
        try:
            return runtime.tieba.archives.request(tid)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get('/api/tieba/archives/{identity}')
    async def tieba_archive_document(identity: str):
        try:
            return runtime.tieba.archives.document(identity)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get('/api/tieba/archives/{identity}/export')
    async def tieba_archive_export(identity: str, kind: str = 'text'):
        try:
            record = runtime.tieba.archives.document(identity)
            if not record['text_bytes']:
                raise ValueError('原文尚未保存，请稍后再下载')
            if kind not in ('text', 'json'):
                raise ValueError('请选择原文或原始记录')
            text = record['original_text'] if kind == 'text' else json.dumps(record, ensure_ascii=False, indent=2)
            return Response(text, media_type='text/plain' if kind == 'text' else 'application/json',
                headers={'Content-Disposition': 'attachment; filename="tieba-' + record['tid'] + ('.txt"' if kind == 'text' else '.json"')})
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post('/api/tieba/archives/{identity}/retry')
    async def tieba_archive_retry(identity: str):
        try:
            return runtime.tieba.archives.retry(identity)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get('/api/tieba/archives/{identity}/images/{image_id}')
    async def tieba_archive_image(identity: str, image_id: str, thumb: bool = False, download: bool = False):
        try:
            path, mime = runtime.tieba.archives.image_file(identity, image_id)
            if thumb:
                def thumbnail():
                    from PIL import Image, ImageOps
                    with Image.open(path) as source:
                        image = ImageOps.exif_transpose(source)
                        image.thumbnail((480, 480))
                        image = image.convert('RGB')
                        output = io.BytesIO()
                        image.save(output, format='JPEG', quality=80)
                        return output.getvalue()
                return Response(await asyncio.to_thread(thumbnail), media_type='image/jpeg')
            extension = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif', 'image/webp': '.webp'}[mime]
            return FileResponse(path, media_type=mime, filename=image_id + extension if download else None)
        except (ValueError, OSError) as exc:
            raise HTTPException(404, '图片尚未保存成功或不可读取') from exc

    @app.post('/api/tieba/{kind}/{identity}')
    async def tieba_edit(kind: str, identity: str, body: TiebaEditRequest):
        try:
            return runtime.tieba.edit(kind, identity, body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get('/images.css')
    async def images_styles():
        return FileResponse(root/'whalegirl_linux'/'images.css', media_type='text/css')

    @app.get('/api/images')
    async def images_state():
        return runtime.images.snapshot()

    @app.post('/api/images/config')
    async def images_config(body: ImageConfigRequest):
        allowed = {n for n in Settings.model_fields if n.startswith(('image_', 'images_', 'moments_image_'))}
        if set(body.values) - allowed:
            raise HTTPException(400, '图片设置包含未知参数')
        try:
            settings = Settings.model_validate({**config.settings.model_dump(), **body.values})
            config.save(settings)
            config.save_image_key(body.api_key)
            runtime.images.retention.next_scan = 0
            return config.public()
        except ValueError as exc:
            raise HTTPException(400, str(exc)[:200]) from exc

    @app.post('/api/images/upload')
    async def images_upload(body: MomentsAssetRequest):
        try:
            raw = base64.b64decode(body.data, validate=True)
            return await asyncio.to_thread(runtime.images.save_asset, raw, scope='global')
        except (ValueError, OSError) as exc:
            raise HTTPException(400, '无法读取图片，请上传普通 PNG / JPG / WebP 图片') from exc

    @app.post('/api/images/references/upload')
    async def reference_upload(body: ReferenceUploadRequest):
        try:
            raw = base64.b64decode(body.data, validate=True)
            return await asyncio.to_thread(runtime.images.references.add, raw, body.variant, body.name, body.role)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, '无法保存参考图，请检查文件或每套参考图数量') from exc

    @app.post('/api/images/references/{identity}')
    async def reference_edit(identity: str, body: ReferenceEditRequest):
        try:
            runtime.images.references.update(identity, **body.model_dump())
            return runtime.images.references.public()
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get('/api/images/references/{identity}/preview')
    async def reference_preview(identity: str):
        library = runtime.images.references
        if not any(v['id'] == identity for v in library.manifest()['items']):
            raise HTTPException(404, '参考图不存在')
        path = library.path(identity)
        if not path.is_file():
            raise HTTPException(404, '参考图文件不可用')
        import io
        from PIL import Image
        def thumbnail():
            with Image.open(path) as im:
                im.thumbnail((480,480)); out=io.BytesIO();im.save(out,format='JPEG',quality=82)
                return out.getvalue()
        return Response(await asyncio.to_thread(thumbnail), media_type='image/jpeg')

    @app.get('/api/images/assets/{identity}/preview')
    async def image_preview(identity: str, thumb: bool = False):
        asset = runtime.images.asset(identity)
        if not asset or not runtime.images.path(asset).is_file():
            raise HTTPException(404, '图片不存在')
        if thumb:
            import io
            from PIL import Image
            def thumbnail():
                with Image.open(runtime.images.path(asset)) as im:
                    im.thumbnail((360,360)); out=io.BytesIO();im.save(out,format='JPEG',quality=80)
                    return out.getvalue()
            return Response(await asyncio.to_thread(thumbnail), media_type='image/jpeg')
        return FileResponse(runtime.images.path(asset), media_type='image/png')

    @app.post('/api/images/assets/{identity}')
    async def image_edit(identity: str, body: ImageEditRequest):
        try:
            runtime.images.edit_asset(identity, body.model_dump(exclude_unset=True))
            return runtime.images.asset(identity)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get('/api/images/assets/{identity}/deletion')
    async def image_deletion_plan(identity: str):
        try:
            return runtime.images.retention.plan(identity)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post('/api/images/assets/{identity}/delete')
    async def image_delete(identity: str, body: ImageDeleteRequest):
        if identity != body.confirm_id:
            raise HTTPException(400, '删除确认与图片不匹配')
        try:
            return runtime.images.retention.delete(identity)
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post('/api/images/jobs')
    async def image_task(body: ImageTaskRequest):
        try:
            if body.destination == 'moments':
                row = runtime.moments.book.get(config.settings.account_id, body.moment_id)
                if not row or row['state'] != 'draft' or row['kind'] != 'publish':
                    raise ValueError('请先选择一条待审的朋友圈发布草稿')
                if body.kind != 'generate' or not config.settings.moments_image_generate_enabled:
                    raise ValueError('请先开启朋友圈生成新配图功能')
                async with runtime.moments.lock:
                    row = runtime.moments.book.get(config.settings.account_id, body.moment_id)
                    if not row or row['state'] != 'draft' or row['kind'] != 'publish':
                        raise ValueError('朋友圈草稿状态已变化，请重新选择')
                    identity = runtime.images.enqueue(**body.model_dump(), admin_authorized=True)
                    runtime.moments.book.change(row['id'], 'awaiting_image')
            else:
                identity = runtime.images.enqueue(**body.model_dump(), admin_authorized=True)
            return {'id': identity, **runtime.images.snapshot()}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post('/api/images/jobs/{identity}')
    async def image_task_action(identity: str, body: ImageActionRequest):
        row = runtime.images.get(identity)
        if not row:
            raise HTTPException(404, '图片任务不存在')
        if body.op == 'cancel':
            if row['state'] not in ('queued', 'generating', 'ready'):
                raise HTTPException(409, '当前任务不能取消')
            runtime.images.change(identity, state='cancelled', error='管理员取消；已提交的生图可能仍计费，结果仅保存图库')
            runtime.images.moment_failed(row, '图片任务已取消，原文保留')
        elif body.op in ('received', 'not_received'):
            if row['state'] != 'uncertain':
                raise HTTPException(409, '仅待核实任务可以人工确认')
            state = 'sent' if body.op == 'received' else 'failed'
            runtime.images.change(identity, state=state, error='管理员核对；不会自动重新生成或发送')
        else:
            raise HTTPException(400, '请在图片卡片上重新整理描述')
        return runtime.images.snapshot()

    @app.post('/api/images/assets/{identity}/analyze')
    async def image_reanalyze(identity: str):
        asset = runtime.images.asset(identity)
        if not asset or asset['state'] == 'tagging':
            raise HTTPException(409, '图片不存在或正在识别')
        with store.lock:
            store.db.execute("UPDATE picture_assets SET state='pending',error='' WHERE id=?", (identity,))
            store.db.commit()
        return runtime.images.asset(identity)

    @app.get('/api/moments')
    async def moments_state():
        return runtime.moments.snapshot()

    @app.post('/api/moments/scan')
    async def moments_scan():
        try:
            async with runtime.moments.lock:
                result = await runtime.moments.scan()
            return {**runtime.moments.snapshot(), 'posts': result.get('posts', [])}
        except (ValueError, OSError, httpx.HTTPError) as exc:
            raise HTTPException(409, str(exc)[:200]) from exc

    @app.post('/api/moments/draft')
    async def moments_draft(body: MomentsDraftRequest):
        payload = {}
        if body.kind != 'publish':
            post = next((p for p in runtime.moments.posts if p['id'] == body.post_id), None)
            if not post:
                raise HTTPException(409, '请先刷新朋友圈并选择来源动态')
            payload = {'post': post}
            if body.kind == 'reply_own':
                comment = next((c for c in post['comments'] if c['id'] == body.comment_id), None)
                if not comment:
                    raise HTTPException(409, '来源评论已变化，请重新刷新')
                payload['comment'] = comment
        try:
            row = await runtime.moments.generate(body.kind, payload)
            return {'id': row['id'] if row else None, **runtime.moments.snapshot()}
        except (ValueError, OSError, httpx.HTTPError) as exc:
            raise HTTPException(409, str(exc)[:200]) from exc

    @app.post('/api/moments/actions/{identity}')
    async def moments_action(identity: str, body: MomentsActionRequest):
        book = runtime.moments.book
        async with runtime.moments.lock:
            row = book.get(config.settings.account_id, identity)
            if not row:
                raise HTTPException(404, '朋友圈记录不存在')
            if body.op in ('publish', 'retry'):
                pass  # send() obtains the same lock after this block.
            elif body.op in ('received', 'not_received'):
                if row['state'] != 'uncertain':
                    raise HTTPException(409, '只有待核对记录可以人工确认')
                book.change(identity, 'sent' if body.op == 'received' else 'failed',
                            receipt='manual-confirmed' if body.op == 'received' else '',
                            error='管理员确认已发送' if body.op == 'received' else '管理员确认未发送，不自动重发')
            elif body.op == 'without_image':
                if row['state'] != 'image_failed':
                    raise HTTPException(409, '只有配图失败的记录可以恢复')
                book.change(identity, 'draft', error='管理员恢复原文草稿，可重新选择现成图片')
            elif row['state'] != 'draft':
                raise HTTPException(409, '只有待审草稿可以编辑或取消')
            elif body.op == 'cancel':
                book.change(identity, 'cancelled', error='管理员取消，记录保留')
            else:
                limit = 180 if row['kind'] == 'publish' else 100
                if not body.text.strip() or len(body.text) > limit:
                    raise HTTPException(400, '文字不能为空且不得超过' + str(limit) + '字')
                payload = json.loads(row['payload'])
                payload.pop('publish_requested_at', None)
                payload['auto_publish'] = False
                if body.assets is not None:
                    if row['kind'] != 'publish':
                        raise HTTPException(400, '评论不能附带图片')
                    for asset_id in body.assets:
                        runtime.images.stage(asset_id)
                    payload['assets'] = list(dict.fromkeys(body.assets))
                    payload['assets_authorized'] = list(payload['assets'])
                with store.lock:
                    store.db.execute('UPDATE moments_actions SET payload=? WHERE id=?',
                        (json.dumps(payload, ensure_ascii=False), identity))
                    store.db.commit()
                book.change(identity, 'draft', text=body.text.strip())
        if body.op == 'retry':
            try:
                created = await runtime.moments.retry_draft(identity)
                return {'id':created, **runtime.moments.snapshot()}
            except (ValueError, OSError, httpx.HTTPError) as exc:
                raise HTTPException(409, str(exc)[:200]) from exc
        if body.op == 'publish':
            try:
                await runtime.moments.send(identity, requested=True)
            except (ValueError, OSError, httpx.HTTPError) as exc:
                raise HTTPException(409, str(exc)[:200]) from exc
        return runtime.moments.snapshot()

    @app.get('/api/moments/assets')
    async def moments_assets():
        return {'assets':runtime.images.assets()}

    @app.post('/api/moments/assets')
    async def moments_upload(body: MomentsAssetRequest):
        return await images_upload(body)

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
            previous_settings = config.settings
            settings.active_persona_id = previous_settings.active_persona_id
            settings.active_persona_revision = previous_settings.active_persona_revision
            if settings.system_prompt_override != previous_settings.system_prompt_override:
                settings.active_persona_id = ''
                settings.active_persona_revision = 0
            config.save(settings, body.api_key)
            runtime.moments.settings_changed(previous_settings, settings)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if runtime.paused_reason in ('模型凭据无效','模型账户余额不足','模型接口权限不足'):
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
        config.save(config.settings.model_copy(update={"system_prompt_override": body.system_prompt.strip(),
                                                     'active_persona_id':'','active_persona_revision':0}))
        return prompt_state()

    @app.post("/api/model/test")
    async def test_model():
        if not config.api_key():
            raise HTTPException(400, "先保存 API Key")
        engine = await runtime.model()
        limit = max(128, config.settings.max_tokens) if engine.api_style == "responses" else 32
        started = time.monotonic()
        text = await engine.chat([{"role": "user", "content": "只回复：连接正常"}], max_tokens=limit,
                                 operation='connection_test')
        return {"ok": bool(text), "message": text[:100] if text else engine.last_error or "模型未返回内容",
                "api_style": engine.api_style, "stream_enabled": engine.stream_enabled,
                "reasoning_effort": engine.reasoning_effort,
                "elapsed_ms": round((time.monotonic()-started)*1000),
                "first_text_ms": engine.last_first_token_ms}

    @app.post("/api/gateway/login")
    async def login():
        status = await transport.status()
        if status.get("status") == "logged_in" and status.get("loggedInUser"):
            return transport.login
        if not runtime.safety.login_request(config.settings.account_id,config.settings):
            raise HTTPException(429,'本地登录保护：10分钟内最多请求7次登录，请稍后再试')
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
    async def recent(include_ignored: bool = False):
        return store.recent(include_ignored=include_ignored)

    @app.get('/api/sticker-library')
    def sticker_library_state():
        s=config.settings
        with store.lock:return runtime.sticker_library.inventory(s.account_id,s.allowed_chat_ids,s)

    @app.get('/api/sticker-library/preview/{md5}')
    async def sticker_preview(md5: str):
        if not re.fullmatch('[0-9a-f]{32}',md5):
            raise HTTPException(400,'表情 ID 无效')
        s=config.settings
        library=runtime.sticker_library
        candidate=store.db.execute('SELECT * FROM sticker_candidates WHERE account=? AND md5=?',(s.account_id,md5)).fetchone()
        if md5 not in runtime.stickers.available and (not candidate or candidate['chat'] not in s.allowed_chat_ids):
            raise HTTPException(404,'表情不存在或来源会话未授权')
        try:
            data=library.assets.preview_data(md5)
            if not data:
                if md5 in runtime.stickers.available:
                    media=await runtime.gateway.native_request('/favorite-media',md5=md5)
                else:
                    source=store.db.execute('SELECT local_id FROM message_log WHERE account=? AND chat=? AND key=?',(s.account_id,candidate['chat'],candidate['source_key'])).fetchone()
                    if not source:raise ValueError('来源消息已不可用')
                    media=await runtime.gateway.media(candidate['chat'],source['local_id'],kind=47)
                if not media.get('data'):raise ValueError('原生表情预览暂不可用')
                library.assets.preview(md5,base64.b64decode(media['data'],validate=True))
                data=library.assets.preview_data(md5)
            return {'data':data}
        except (OSError,ValueError,httpx.HTTPError) as exc:
            raise HTTPException(409,'预览暂不可用：'+type(exc).__name__) from exc

    @app.post('/api/sticker-library/batch')
    def sticker_batch(body: StickerBatchRequest):
        s=config.settings
        results=[]
        for md5 in dict.fromkeys(body.ids):
            try:
                if not re.fullmatch('[0-9a-f]{32}',md5):raise ValueError('表情 ID 无效')
                if body.op=='retry':runtime.sticker_library.retry(s.account_id,md5,s.allowed_chat_ids)
                else:runtime.sticker_library.skip(s.account_id,md5,s.allowed_chat_ids)
                results.append({'id':md5,'ok':True})
            except ValueError as exc:results.append({'id':md5,'ok':False,'error':str(exc)[:180]})
        return {'results':results}

    @app.post('/api/sticker-library/manage')
    def manage_sticker(body: StickerLibraryRequest):
        s=config.settings
        try:
            if body.chat and body.chat not in s.allowed_chat_ids:raise ValueError('该会话未授权')
            if body.op=='retry':
                runtime.sticker_library.retry(s.account_id,body.id,s.allowed_chat_ids)
                return {'ok':True}
            if body.op=='skip':
                runtime.sticker_library.skip(s.account_id,body.id,s.allowed_chat_ids)
                return {'ok':True}
            return runtime.sticker_library.change(s.account_id,body.id,body.op,body.reason,version=body.version,chat=body.chat)
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
        try:return runtime.sticker_library.edit_profile(config.settings.account_id,md5,body)
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

    @app.get('/api/learning')
    def learning_list(state: str='', q: str='', chat: str='', kind: str=''):
        try:
            return runtime.learning.snapshot(state=state, query=q[:200], chat=chat, kind=kind)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get('/api/persona-state')
    def get_persona_state(chat: str=''):
        return runtime.persona_live_snapshot(chat=chat)

    @app.post('/api/persona-state')
    def update_persona_state(body: PersonaStateRequest):
        s = config.settings
        try:
            return admin_persona_state_action(
                store, s, s.account_id, s.allowed_chat_ids,
                body.model_dump(exclude_none=True), tieba_service=runtime.tieba
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get('/api/learning/{identity}')
    def learning_detail(identity: int):
        s = config.settings
        result = runtime.learning.book.detail(s.account_id, s.allowed_chat_ids, identity)
        if not result:
            raise HTTPException(404, '学习记录不存在或会话未授权')
        return result

    @app.post('/api/learning/{identity}')
    def learning_change(identity: int, body: LearningRequest):
        s = config.settings
        try:
            return runtime.learning.book.change(s.account_id, s.allowed_chat_ids, identity,
                                                body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get('/api/memory')
    def memories(q: str='', include_archived: bool=False, chat: str=''):
        s=config.settings
        if chat and chat not in s.allowed_chat_ids:raise HTTPException(403,'该会话未授权')
        people={r[0] for r in store.db.execute('SELECT DISTINCT sender_id FROM message_log WHERE account=? AND chat=?',(s.account_id,chat))} if chat else set()
        rows=runtime.memory_book.search(s.account_id,s.allowed_chat_ids,q[:200],archived=include_archived,limit=100,
            chat=chat or None,subjects=people,shared=s.global_memory and s.memory_scope_mode=='shared_profiles')
        return {'memories':rows,
                'revisions':runtime.memory_book.revisions(s.account_id,s.allowed_chat_ids),'ai_editing':s.editable_memory_enabled}

    @app.get('/api/memory-hub')
    def memory_hub_list(q: str='',chat: str='',subject: str='',tier: str='',status: str='',kind: str='',view: str='',before: int=0):
        try:
            return runtime.memory_hub.snapshot(config.settings,query=q[:200],chat=chat,subject=subject,
                tier=tier,status=status,kind=kind,view=view,before=before)
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc

    @app.get('/api/memory-hub/{identity}')
    def memory_hub_detail(identity: int):
        s=config.settings
        result=runtime.memory_hub.detail(s.account_id,s.allowed_chat_ids,identity)
        if not result:raise HTTPException(404,'记忆不存在或会话未授权')
        return result

    @app.post('/api/memory-hub')
    def memory_hub_change(body: MemoryRequest):
        s=config.settings
        try:return runtime.memory_book.change(s.account_id,s.allowed_chat_ids,body.model_dump(exclude_none=True),actor='admin')
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc

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
        return {"ok": True, "automatic_resend": False}

    return app
