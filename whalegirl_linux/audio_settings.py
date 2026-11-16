"""Independent, revisioned audio settings; no chat-engine configuration changes."""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AudioProvider(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: str = Field(pattern='^(speech|music)$')
    backend: str = Field(default='nyro', pattern='^(nyro|pc_bridge)$')
    api_url: str = 'https://sapi.nyro.lol'
    model: str = Field(min_length=1, max_length=120)

    @field_validator('api_url')
    @classmethod
    def endpoint(cls, value):
        value = value.strip().rstrip('/')
        return value

    @model_validator(mode='after')
    def protocol(self):
        value = self.api_url
        if self.backend == 'pc_bridge':
            if self.kind != 'speech' or value != 'http://127.0.0.1:19881':
                raise ValueError('电脑语音仅允许专用的本机加密通道地址 http://127.0.0.1:19881')
            return self
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment):
            raise ValueError('音频接口需使用无凭据、查询参数或片段的 HTTPS 地址')
        if parsed.path not in ('', '/v1', '/v1/chat/completions'):
            raise ValueError('请输入根地址、/v1 或完整 /v1/chat/completions 地址')
        return self

    def endpoint_url(self):
        if self.backend == 'pc_bridge':
            return self.api_url + '/v1/tts'
        root = self.api_url.removesuffix('/v1/chat/completions').removesuffix('/v1')
        return root + '/v1/chat/completions'


class VoicePreset(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(default='Leda', min_length=1, max_length=40)
    voice: str = Field(default='Leda', min_length=1, max_length=80)
    pitch: float = Field(default=0, ge=-3, le=3, allow_inf_nan=False)
    speed: float = Field(default=1, ge=.7, le=1.4, allow_inf_nan=False)
    gain_db: float = Field(default=0, ge=-12, le=6, allow_inf_nan=False)


class SpeechDelivery(BaseModel):
    """Per-utterance adjustments relative to the administrator's voice preset."""
    model_config = ConfigDict(extra='forbid')
    speed: float = Field(default=1, ge=.85, le=1.2, allow_inf_nan=False)
    pitch: float = Field(default=0, ge=-1.5, le=1.5, allow_inf_nan=False)
    gain_db: float = Field(default=0, ge=-4, le=3, allow_inf_nan=False)
    pause_seconds: float = Field(default=.3, ge=.12, le=.5, allow_inf_nan=False)

    def apply(self, base):
        return VoicePreset.model_validate({**base.model_dump(),
            'speed': round(max(.7, min(1.4, base.speed * self.speed)), 4),
            'pitch': round(max(-3, min(3, base.pitch + self.pitch)), 4),
            'gain_db': round(max(-12, min(6, base.gain_db + self.gain_db)), 4)})


class AudioSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = False
    voice_enabled: bool = True
    music_enabled: bool = True
    voice_mode: str = Field(default='auto', pattern='^(auto|requested)$')
    ai_voice_dynamics_enabled: bool = False
    music_mode: str = Field(default='requested', pattern='^(auto|requested)$')
    allowed_chat_ids: list[str] = Field(default_factory=list, max_length=100)
    speech_profile: str = 'speech'
    music_profile: str = 'music'
    providers: dict[str, AudioProvider] = Field(default_factory=lambda: {
        'speech': AudioProvider(kind='speech', model='gemini-3.8-flash-tts'),
        'music': AudioProvider(kind='music', model='lyria-3.5'),
        'music-pro': AudioProvider(kind='music', model='lyria-3-pro'),
    })
    presets: dict[str, VoicePreset] = Field(default_factory=lambda: {'default': VoicePreset()})
    default_preset: str = 'default'
    ai_preset_ids: list[str] = Field(default_factory=list, max_length=20)
    singing_style: str = Field(default='中文清亮柔和女声，自然演唱', max_length=400)
    voice_max_seconds: int = Field(default=45, ge=5, le=50)
    voice_overflow: str = Field(default='file', pattern='^(file|hold)$')
    music_duration: int = Field(default=30, ge=5, le=180)
    request_timeout: int = Field(default=300, ge=30, le=600)
    max_download_mb: int = Field(default=16, ge=1, le=32)
    cache_mb: int = Field(default=128, ge=32, le=1024)
    disk_reserve_mb: int = Field(default=512, ge=256, le=4096)

    @model_validator(mode='after')
    def references(self):
        for values in (self.providers, self.presets):
            if not 1 <= len(values) <= 20 or any(not re.fullmatch('[a-zA-Z0-9_-]{1,40}', key) for key in values):
                raise ValueError('配置与预设 ID 需为 1～40 位字母、数字、下划线或连字符，最多20项')
        if self.speech_profile not in self.providers or self.providers[self.speech_profile].kind != 'speech':
            raise ValueError('语音模型配置不存在或类型不符')
        if self.music_profile not in self.providers or self.providers[self.music_profile].kind != 'music':
            raise ValueError('音乐模型配置不存在或类型不符')
        if self.default_preset not in self.presets or any(key not in self.presets for key in self.ai_preset_ids):
            raise ValueError('默认声线或 AI 可选声线不存在')
        if self.cache_mb < self.max_download_mb + 64:
            raise ValueError('缓存容量至少为下载上限加64 MiB，以预留原文件和转码成品')
        return self


class AudioConfig:
    def __init__(self, config, store):
        self.config, self.store = config, store
        with store.lock, store.db:
            store.db.executescript('''
                CREATE TABLE IF NOT EXISTS audio_config(
                    id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,
                    settings_json TEXT NOT NULL,secrets_json TEXT NOT NULL,updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS audio_config_revisions(
                    revision INTEGER PRIMARY KEY,settings_json TEXT NOT NULL,updated REAL NOT NULL);
            ''')
            store.db.execute('INSERT OR IGNORE INTO audio_config VALUES(1,0,?,?,?)',
                             (AudioSettings().model_dump_json(), '{}', time.time()))

    def read(self):
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM audio_config WHERE id=1').fetchone()
            return dict(row)

    def snapshot(self):
        row = self.read()
        return {'revision': row['revision'], 'settings': json.loads(row['settings_json']),
                'keys_configured': {key: bool(value) for key, value in json.loads(row['secrets_json']).items()},
                'updated': row['updated']}

    def current(self):
        row = self.read()
        return row['revision'], AudioSettings.model_validate_json(row['settings_json'])

    def save(self, revision, values, keys=None):
        settings = AudioSettings.model_validate(values)
        for key, value in (keys or {}).items():
            if key not in settings.providers or not isinstance(value, str) or len(value) > 4096:
                raise ValueError('音频密钥配置无效')
        with self.store.lock, self.store.db:
            row = self.read()
            if row['revision'] != revision:
                raise ValueError('声线配置已被另一页面修改，请刷新后重试')
            secrets = json.loads(row['secrets_json'])
            for key, value in (keys or {}).items():
                # Blank preserves a key; explicit null is not accepted.
                if value.strip():
                    secrets[key] = self.config.fernet.encrypt(value.strip().encode()).decode()
            now = time.time()
            self.store.db.execute('INSERT OR IGNORE INTO audio_config_revisions VALUES(?,?,?)',
                                  (revision, row['settings_json'], row['updated']))
            changed = self.store.db.execute('UPDATE audio_config SET revision=?,settings_json=?,secrets_json=?,updated=? '
                'WHERE id=1 AND revision=?', (revision + 1, settings.model_dump_json(), json.dumps(secrets), now, revision))
            if changed.rowcount != 1:
                raise ValueError('声线配置发生冲突，请刷新')
        return self.snapshot()

    def capture(self, kind, preset_id=''):
        row = self.read()
        settings = AudioSettings.model_validate_json(row['settings_json'])
        profile_id = settings.speech_profile if kind == 'speech' else settings.music_profile
        encrypted = json.loads(row['secrets_json']).get(profile_id)
        if not encrypted:
            raise ValueError('对应音频模型尚未配置独立 API Key')
        key = self.config.fernet.decrypt(encrypted.encode()).decode()
        preset_id = preset_id or settings.default_preset
        if preset_id not in settings.presets:
            raise ValueError('声线预设已不存在')
        return {'revision': row['revision'], 'settings': settings, 'profile_id': profile_id,
                'provider': settings.providers[profile_id], 'key': key,
                'preset_id': preset_id, 'preset': settings.presets[preset_id]}

    def history(self):
        with self.store.lock:
            return [dict(row) for row in self.store.db.execute(
                'SELECT revision,updated FROM audio_config_revisions ORDER BY revision DESC LIMIT 10')]

    def restore(self, revision, target):
        with self.store.lock:
            row = self.store.db.execute('SELECT settings_json FROM audio_config_revisions WHERE revision=?', (target,)).fetchone()
        if not row:
            raise ValueError('声线历史版本不存在')
        return self.save(revision, json.loads(row[0]))
