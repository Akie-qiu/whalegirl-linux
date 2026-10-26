from __future__ import annotations

import json
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StickerChatPermission(BaseModel):
    model_config = ConfigDict(extra='forbid')
    auto_collect: bool = True
    command_collect: bool = True
    command_disable: bool = True


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    account_id: str = Field(default="", max_length=200)
    allowed_chat_ids: list[str] = Field(default_factory=list, max_length=100)
    keywords: list[str] = Field(default_factory=lambda: ["鲸鱼娘", "小鲸鱼", "deepseek"], max_length=30)
    require_mention_or_keyword: bool = True
    natural_group_replies: bool = False
    trigger_only_chat_ids: list[str] = Field(default_factory=list, max_length=100)
    system_prompt_override: str = Field(default="", max_length=16000)
    active_persona_id: str = Field(default='', max_length=80, pattern=r'^[a-zA-Z0-9_-]{0,80}$')
    active_persona_revision: int = Field(default=0, ge=0)
    quote_replies_enabled: bool = True
    group_mentions_enabled: bool = True
    safety_enabled: bool = True
    safety_send_limits_enabled: bool = True
    safety_per_minute: int = Field(default=8, ge=0, le=60)
    safety_per_hour: int = Field(default=120, ge=0, le=1200)
    safety_chat_per_hour: int = Field(default=40, ge=0, le=600)
    safety_per_day: int = Field(default=500, ge=0, le=10000)
    safety_duplicate_seconds: int = Field(default=180, ge=30, le=1800)
    stickers_enabled: bool = True
    sticker_aggressiveness: Literal['low','normal','high'] = 'normal'
    sticker_after_text: Literal['off','normal','high'] = 'high'
    sticker_pair_probability: int = Field(default=90, ge=0, le=100)
    api_url: str = "https://api.deepseek.com/chat/completions"
    api_style: Literal['auto', 'openai', 'responses', 'anthropic'] = 'auto'
    reasoning_effort: Literal['default', 'low', 'medium', 'high'] = 'default'
    model_stream_enabled: bool = False
    model: str = Field(default="deepseek-chat", min_length=1, max_length=100)
    max_tokens: int = Field(default=220, ge=32, le=1200)
    history_pairs: int = Field(default=5, ge=0, le=15)
    min_send_interval: float = Field(default=6, ge=3, le=120)
    chat_cooldown: float = Field(default=15, ge=5, le=300)
    poll_interval: float = Field(default=1, ge=0.25, le=60)
    context_messages: int = Field(default=28, ge=10, le=150)
    global_memory: bool = True
    memory_scope_mode: Literal['shared_profiles', 'isolated'] = 'shared_profiles'
    memory_auto_capture: bool = True
    memory_recall_limit: int = Field(default=8, ge=2, le=20)
    history_recall_limit: int = Field(default=6, ge=0, le=20)
    context_char_budget: int = Field(default=14000, ge=4000, le=40000)
    background_digest_enabled: bool = True
    colloquialism: Literal['low','normal','high'] = 'high'
    teasing: Literal['low','normal','high'] = 'normal'
    persona_density: Literal['low','normal','high'] = 'low'
    followup_questions: Literal['low','normal','high'] = 'low'
    reply_length: Literal['short','normal','detailed'] = 'short'
    reply_warmth: Literal['low','normal','high'] = 'high'
    banter_level: Literal['low','normal','high'] = 'normal'
    play_along: Literal['low','normal','high'] = 'high'
    callback_frequency: Literal['off','low','normal','high'] = 'normal'
    assertiveness: Literal['low','normal','high'] = 'normal'
    affection_enabled: bool = True
    affection_initial_score: int = Field(default=40, ge=0, le=100)
    affection_daily_gain: int = Field(default=4, ge=0, le=20)
    affection_daily_loss: int = Field(default=6, ge=0, le=20)
    affection_cooldown_seconds: int = Field(default=600, ge=60, le=86400)
    affection_private_style: Literal['warm','close'] = 'close'
    sticker_candidate_limit: int = Field(default=8, ge=4, le=20)
    sticker_cooldown_seconds: int = Field(default=180, ge=0, le=3600)
    sticker_self_manage_enabled: bool = True
    sticker_auto_collect_enabled: bool = True
    sticker_auto_disable_enabled: bool = True
    sticker_library_limit: int = Field(default=60, ge=10, le=200)
    sticker_collect_per_day: int = Field(default=6, ge=0, le=30)
    sticker_disable_per_day: int = Field(default=3, ge=0, le=30)
    sticker_collect_aggressiveness: Literal['low','normal','high'] = 'normal'
    sticker_command_collect_enabled: bool = True
    sticker_command_disable_enabled: bool = True
    sticker_chat_permissions: dict[str, StickerChatPermission] = Field(default_factory=dict, max_length=200)
    sticker_cache_cleanup_enabled: bool = True
    sticker_failed_cache_hours: int = Field(default=24, ge=1, le=168)
    chat_habits_enabled: bool = True
    debounce_single_seconds: float = Field(default=0.65, ge=0, le=3)
    debounce_burst_seconds: float = Field(default=1.6, ge=0, le=5)
    debounce_max_seconds: float = Field(default=4, ge=0, le=8)
    reply_max_parts: int = Field(default=3, ge=1, le=3)
    reply_part_interval: float = Field(default=0.8, ge=0.3, le=3)
    participation: Literal['quiet','balanced','active'] = 'balanced'
    interest_keywords: list[str] = Field(default_factory=lambda: ['游戏','八卦','表情包'], max_length=30)
    quiet_start_hour: int = Field(default=1, ge=0, le=23)
    quiet_end_hour: int = Field(default=8, ge=0, le=23)
    proactive_enabled: bool = False
    proactive_private_enabled: bool = False
    proactive_chat_ids: list[str] = Field(default_factory=list, max_length=100)
    proactive_strict_chat_ids: list[str] = Field(default_factory=list, max_length=100)
    proactive_mentions_enabled: bool = False
    proactive_level: Literal['quiet', 'balanced', 'active'] = 'balanced'
    proactive_per_day: int = Field(default=12, ge=0, le=100)
    proactive_chat_per_day: int = Field(default=0, ge=0, le=100)
    proactive_cooldown_minutes: int = Field(default=0, ge=0, le=1440)
    proactive_decision_minutes: int = Field(default=5, ge=3, le=180)
    proactive_idle_minutes: int = Field(default=0, ge=0, le=180)
    proactive_start_hour: int = Field(default=9, ge=0, le=23)
    proactive_end_hour: int = Field(default=23, ge=0, le=23)
    proactive_continuation_minutes: int = Field(default=10, ge=1, le=30)
    proactive_pause_hours: int = Field(default=24, ge=1, le=168)
    repeat_probability: float = Field(default=0.4, ge=0, le=1)
    editable_memory_enabled: bool = True
    memory_self_capture_enabled: bool = True
    memory_half_life_days: int = Field(default=21, ge=3, le=180)
    learning_enabled: bool = True
    learning_eagerness: int = Field(default=70, ge=0, le=100)
    learning_auto_accept: bool = True
    learning_default_ttl_days: int = Field(default=30, ge=1, le=365)
    learning_recall_limit: int = Field(default=4, ge=1, le=8)
    evolution_preset: Literal['conservative', 'balanced', 'aggressive', 'custom'] = 'balanced'
    evolution_async_digest: bool = True
    evolution_slang_auto_accept: bool = True
    evolution_profile_auto_accept: bool = True
    evolution_group_mask_auto_accept: bool = True
    evolution_confidence_threshold: float = Field(default=0.85, ge=0.5, le=0.99)
    evolution_min_distinct_users: int = Field(default=2, ge=1, le=5)
    evolution_min_occurrences: int = Field(default=2, ge=1, le=10)
    mood_engine_enabled: bool = True
    mood_volatility: Literal['low', 'normal', 'high'] = 'normal'
    mood_half_life_minutes: int = Field(default=25, ge=5, le=180)
    daily_life_theater_enabled: bool = True
    anti_formula_guard_enabled: bool = True
    chime_in_ultra_short: bool = True
    web_search_enabled: bool = False
    web_search_result_count: int = Field(default=5, ge=1, le=10)
    web_search_timeout: float = Field(default=35, ge=10, le=60)
    text_per_hour: int = Field(default=120, ge=0, le=1200)
    text_per_day: int = Field(default=500, ge=0, le=10000)
    sticker_per_hour: int = Field(default=60, ge=0, le=1200)
    sticker_per_day: int = Field(default=200, ge=0, le=10000)
    vision_enabled: bool = True
    moments_enabled: bool = False
    moments_publish_enabled: bool = False
    moments_reply_own_enabled: bool = False
    moments_comment_friends_enabled: bool = False
    moments_mode: Literal['draft', 'auto'] = 'draft'
    moments_publish_per_day: int = Field(default=1, ge=0, le=10)
    moments_reply_own_per_day: int = Field(default=10, ge=0, le=100)
    moments_comment_friends_per_day: int = Field(default=3, ge=0, le=30)
    moments_poll_minutes: int = Field(default=15, ge=5, le=120)
    moments_start_hour: int = Field(default=9, ge=0, le=23)
    moments_end_hour: int = Field(default=23, ge=0, le=23)
    moments_prompt: str = Field(default='', max_length=8000)
    moments_topics: list[str] = Field(default_factory=lambda: ['游戏', '日常碎碎念'], max_length=30)
    moments_friend_ids: list[str] = Field(default_factory=list, max_length=100)
    moments_asset_ids: list[str] = Field(default_factory=list, max_length=9)
    images_chat_enabled: bool = True
    image_generation_enabled: bool = True
    image_api_url: str = 'https://image.ai-zjl.cc/v1'
    image_model: str = Field(default='gpt-image-2.5', min_length=1, max_length=120)
    image_size: str = Field(default='1024x1024', pattern=r'^(auto|[0-9]{3,4}x[0-9]{3,4})$')
    image_timeout: int = Field(default=300, ge=120, le=600)
    image_per_day: int = Field(default=10, ge=0, le=100)
    image_retention_uploaded_days: int = Field(default=0, ge=0, le=3650)
    image_retention_generated_days: int = Field(default=0, ge=0, le=3650)
    image_retention_tieba_days: int = Field(default=0, ge=0, le=3650)
    image_references_enabled: bool = True
    image_character_variant: Literal['chibi', 'girl', 'mature'] = 'girl'
    image_character_identity: str = Field(default='大肥鱼，蓝发蓝眼的鲸鱼主题二次元角色，蓝色长发带浅蓝渐变、呆毛和鲸尾。三套外观是同一个自己，人格沿用当前人设。蓝发、蓝眼和鲸鱼特征是身份特征；服装、表情、动作和场景可按本次要求变化。', max_length=1600)
    image_character_chibi: str = Field(default='Q版，圆润可爱的头身比例，保留蓝发蓝眼、呆毛与鲸鱼元素，身体比例以Q版参考图为准。', max_length=1600)
    image_character_girl: str = Field(default='少女版，清爽柔和的二次元少女外观，蓝色长发、浅蓝渐变、蓝眼睛、呆毛与鲸尾，身体比例和默认服饰以少女版参考图为准，服装可按要求更换。', max_length=1600)
    image_character_mature: str = Field(default='偏年长版，同一角色更成熟的面容与身体比例，保留蓝发蓝眼、呆毛与鲸鱼元素，比例以偏年长版参考图为准。', max_length=1600)
    image_reference_mode: Literal['auto', 'edit', 'remix'] = 'auto'
    images_allowed_chat_ids: list[str] = Field(default_factory=list, max_length=100)
    moments_image_pick_enabled: bool = False
    moments_image_generate_enabled: bool = False
    moments_image_generate_eagerness: int = Field(default=75, ge=0, le=100)
    moments_variety_days: int = Field(default=7, ge=1, le=30)
    tieba_enabled: bool = False
    tieba_forums: list[str] = Field(default_factory=lambda: ['绝区零内鬼'], max_length=10)
    tieba_interests: list[str] = Field(default_factory=lambda: ['绝区零', '有趣的游戏讨论', '角色趣事', '梗图'], max_length=30)
    tieba_interest_sensitivity: int = Field(default=70, ge=0, le=100)
    tieba_interval_minutes: int = Field(default=30, ge=10, le=1440)
    tieba_share_enabled: bool = True
    tieba_discovery_share_enabled: bool = True
    tieba_context_share_enabled: bool = True
    tieba_meme_eagerness: int = Field(default=80, ge=0, le=100)
    tieba_recall_eagerness: int = Field(default=80, ge=0, le=100)
    tieba_share_eagerness: int = Field(default=65, ge=0, le=100)
    tieba_unused_days: int = Field(default=30, ge=0, le=3650)
    tieba_trash_days: int = Field(default=7, ge=1, le=90)
    tieba_share_chat_ids: list[str] = Field(default_factory=list, max_length=20)
    tieba_share_per_day: int = Field(default=3, ge=0, le=30)
    tieba_share_cooldown_minutes: int = Field(default=120, ge=10, le=1440)
    tieba_share_threshold: int = Field(default=75, ge=1, le=100)
    tieba_start_hour: int = Field(default=9, ge=0, le=23)
    tieba_end_hour: int = Field(default=23, ge=0, le=23)
    tieba_learn_enabled: bool = True
    tieba_memes_ttl_days: int = Field(default=30, ge=1, le=90)
    tieba_images_enabled: bool = True
    tieba_images_per_day: int = Field(default=3, ge=0, le=20)
    tieba_images_limit: int = Field(default=100, ge=1, le=500)
    tieba_archive_enabled: bool = True
    tieba_archive_limit_mb: int = Field(default=512, ge=64, le=4096)
    expected_wechat_version: Literal["4.1.0.16", "4.1.1.8"] = "4.1.0.16"

    @field_validator("allowed_chat_ids", "keywords", "trigger_only_chat_ids", "interest_keywords", "moments_topics", "moments_friend_ids",
                     "tieba_forums", "tieba_interests", "tieba_share_chat_ids",
                     "proactive_chat_ids", "proactive_strict_chat_ids")
    @classmethod
    def clean_strings(cls, values):
        if any(not v.strip() or len(v) > 200 for v in values):
            raise ValueError("列表项必须为非空文本且不超过 200 字符")
        return list(dict.fromkeys(v.strip() for v in values))

    @field_validator("api_url", "image_api_url")
    @classmethod
    def valid_api_url(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.fragment or parsed.query:
            raise ValueError("模型接口需使用不含内嵌凭据的 HTTPS 地址")
        return value.rstrip("/")

    @field_validator('moments_asset_ids')
    @classmethod
    def valid_moments_assets(cls, values):
        import re
        if any(not re.fullmatch(r'[0-9a-f]{64}', v) for v in values):
            raise ValueError('朋友圈素材 ID 不正确')
        return list(dict.fromkeys(values))


def private_write(path: Path, value: str):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".pending")
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(value)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


class Config:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "settings.json"
        self.settings = Settings.model_validate_json(self.path.read_text("utf-8")) if self.path.exists() else Settings()
        self.admin_token = self._secret("admin.token", lambda: secrets.token_urlsafe(32))
        self.gateway_token = self._secret("gateway.token", lambda: secrets.token_hex(32))
        self.fernet = Fernet(self._secret("encryption.key", lambda: Fernet.generate_key().decode()).encode())
        self.generation = 0
        self.console_sessions = {}

    def authorize_admin(self, supplied):
        if not supplied or len(supplied)>1024:
            return False
        if hmac.compare_digest(supplied.encode(),self.admin_token.encode()):
            return True
        return self.console_sessions.get(hashlib.sha256(supplied.encode()).hexdigest(),0)>time.time()

    def set_console_password(self, password):
        salt = secrets.token_hex(16)
        iterations = 310000
        digest = hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),iterations).hex()
        private_write(self.directory/'console-password.json',json.dumps({
            'algorithm':'pbkdf2-sha256','salt':salt,'iterations':iterations,'digest':digest}))
        self.console_sessions.clear()

    def console_login(self, password):
        valid = hmac.compare_digest(password.encode(),self.admin_token.encode())
        path = self.directory/'console-password.json'
        if not valid and path.exists():
            stored = json.loads(path.read_text())
            digest = hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(stored['salt']),stored['iterations']).hex()
            valid = hmac.compare_digest(digest,stored['digest'])
        if not valid:
            return None
        now = time.time()
        self.console_sessions = {k:v for k,v in self.console_sessions.items() if v>now}
        token = secrets.token_urlsafe(32)
        self.console_sessions[hashlib.sha256(token.encode()).hexdigest()] = now+86400
        return token

    def console_logout(self, supplied):
        self.console_sessions.pop(hashlib.sha256(supplied.encode()).hexdigest(),None)

    def _secret(self, name, factory):
        path = self.directory / name
        if not path.exists():
            private_write(path, factory())
        return path.read_text("utf-8").strip()

    def api_key(self):
        path = self.directory / "api-key.enc"
        return self.fernet.decrypt(path.read_bytes()).decode() if path.exists() else ""

    def image_api_key(self):
        path = self.directory / 'image-api-key.enc'
        return self.fernet.decrypt(path.read_bytes()).decode() if path.exists() else ''

    def save_image_key(self, key):
        if key.strip():
            private_write(self.directory / 'image-api-key.enc', self.fernet.encrypt(key.strip().encode()).decode())

    def tieba_credentials(self):
        path = self.directory / 'tieba-credentials.enc'
        return json.loads(self.fernet.decrypt(path.read_bytes())) if path.exists() else {}

    def save_tieba_credentials(self, bduss='', stoken=''):
        if not bduss.strip() and not stoken.strip():
            return
        values = self.tieba_credentials()
        for name, value in [('BDUSS', bduss), ('STOKEN', stoken)]:
            if value.strip():
                values[name] = value.strip()
        private_write(self.directory / 'tieba-credentials.enc', self.fernet.encrypt(json.dumps(values).encode()).decode())

    def save(self, settings: Settings, api_key: str = ""):
        if settings.enabled and not settings.allowed_chat_ids:
            raise ValueError("先选择允许回复的会话")
        if settings.enabled and not (api_key.strip() or self.api_key()):
            raise ValueError("先配置模型 API Key")
        if api_key.strip():
            private_write(self.directory / "api-key.enc", self.fernet.encrypt(api_key.strip().encode()).decode())
        private_write(self.path, settings.model_dump_json(indent=2))
        self.sync_authority(settings)
        self.settings = settings
        self.generation += 1

    def sync_authority(self, settings: Settings | None = None):
        """Write the native-side authority file read by in-container UI tools."""
        settings = settings or self.settings
        jobs = os.environ.get("WHALEGIRL_UI_JOBS_DIR")
        if jobs and Path(jobs).is_dir():
            private_write(Path(jobs) / "authorized-chats.json", json.dumps({
                "account_id": settings.account_id, "allowed_chat_ids": settings.allowed_chat_ids,
                "group_mentions_enabled": settings.group_mentions_enabled,
                "images_chat_enabled": settings.images_chat_enabled,
                "chat_enabled": settings.enabled,
                "images_allowed_chat_ids": settings.images_allowed_chat_ids,
                "moments_authorized": True, "moments_enabled": settings.moments_enabled,
                "moments_publish_enabled": settings.moments_publish_enabled,
                "moments_reply_own_enabled": settings.moments_reply_own_enabled,
                "moments_comment_friends_enabled": settings.moments_comment_friends_enabled,
                "moments_friend_ids": settings.moments_friend_ids,
                "moments_asset_ids": settings.moments_asset_ids}))

    def public(self):
        return {**self.settings.model_dump(), "api_key_configured": bool(self.api_key()),
                "image_api_key_configured": bool(self.image_api_key())}
