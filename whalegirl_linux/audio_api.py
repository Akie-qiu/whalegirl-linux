"""Audio console routes, protected by the application's existing admin boundary."""
import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from .audio_settings import AudioSettings


class AudioSaveRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    settings: AudioSettings
    keys: dict[str, str] = Field(default_factory=dict, repr=False)


class AudioPreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['speech', 'music'] = 'speech'
    arguments: dict
    acknowledge_charge: bool = False
    request_id: str = Field(default='', pattern='^(|[0-9a-f]{32})$')


class AudioTuneRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_id: str = Field(pattern='^[0-9a-f]{32}$')
    preset_id: str = Field(min_length=1, max_length=40)
    request_id: str = Field(default='', pattern='^(|[0-9a-f]{32})$')


class PcVoiceSelectRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    voice_id: Literal['cyrene', 'yachiyo', 'xiaofei']
    request_id: str = Field(pattern='^[0-9a-f]{32}$')


class AudioActionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation: Literal['cancel', 'retry_download', 'retry_send', 'check_delivery', 'send_file']


class AudioRestoreRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    target: int = Field(ge=0)


class PrivateAudioRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # FastAPI's default 422 body can echo the entire rejected input,
                # including new API keys. Keep audio validation errors private.
                return JSONResponse({'detail': '音频参数无效，请检查字段、数值范围、缓存预留与预设引用'}, 422)
        return private_handler


def install_audio_api(app, runtime):
    service = runtime.audio
    router = APIRouter(route_class=PrivateAudioRoute)

    @router.get('/audio.js')
    async def audio_script():
        return FileResponse(Path(__file__).with_name('audio.js'), media_type='application/javascript')

    @router.get('/api/audio')
    def audio_state():
        return service.snapshot()

    @router.get('/api/audio/pc-status')
    async def pc_voice_state():
        try:
            return await service.pc_status()
        except ValueError:
            return {'connected': False, 'reason': '电脑语音尚未配置或未选择'}

    @router.post('/api/audio/pc-select')
    async def select_pc_voice(body: PcVoiceSelectRequest):
        try:
            return await service.provider.select_pc_voice(service.settings.capture('speech'), body.voice_id, body.request_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.post('/api/audio/config')
    def audio_config(body: AudioSaveRequest):
        try:
            service.settings.save(body.revision, body.settings.model_dump(), body.keys)
            return service.snapshot()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.post('/api/audio/restore')
    def audio_restore(body: AudioRestoreRequest):
        try:
            service.settings.restore(body.revision, body.target)
            return service.snapshot()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.post('/api/audio/preview')
    def audio_preview(body: AudioPreviewRequest):
        _, settings = service.settings.current()
        profile = settings.speech_profile if body.kind == 'speech' else settings.music_profile
        if not body.acknowledge_charge and settings.providers[profile].backend != 'pc_bridge':
            raise HTTPException(400, '生成试听会产生费用，请明确确认')
        try:
            return service.public_job(service.enqueue(body.kind, body.arguments, preview=True, request_id=body.request_id))
        except ValueError:
            raise HTTPException(400, '试听参数或模型密钥未配置，请检查独立音频设置') from None

    @router.post('/api/audio/tune')
    def audio_tune(body: AudioTuneRequest):
        source = service.get(body.source_id)
        if not source:
            raise HTTPException(404, '试听素材不存在')
        try:
            args = json.loads(source['args_json'])
            return service.public_job(service.enqueue('speech', {'text': args.get('text') or '调参试听',
                'voice_preset_id': body.preset_id}, preview=True, render_of=body.source_id, request_id=body.request_id))
        except ValueError:
            raise HTTPException(400, '调参素材或声线预设无效，更换模型音色需重新生成试听') from None

    @router.post('/api/audio/jobs/{identity}/actions')
    async def audio_action(identity: str, body: AudioActionRequest):
        try:
            if body.operation == 'check_delivery':
                return await service.reconcile(identity)
            return service.action(identity, body.operation)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get('/api/audio/jobs/{identity}/media')
    def audio_media(identity: str):
        job = service.get(identity)
        if not job or not job['output_file']:
            raise HTTPException(404, '音频尚未生成或不属于当前账号')
        try:
            path = service.safe_path(job['output_file'])
            if not path.is_file():
                raise ValueError()
        except ValueError:
            raise HTTPException(404, '音频文件不可用') from None
        mime = {'wav': 'audio/wav', 'mp3': 'audio/mpeg', 'm4a': 'audio/mp4'}[job['format']]
        return FileResponse(path, media_type=mime)

    app.include_router(router)
