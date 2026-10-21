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
    quote_replies_enabled: bool = True
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
    api_url: str = "https://api.deepseek.com/chat/completions"
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
    repeat_probability: float = Field(default=0.4, ge=0, le=1)
    editable_memory_enabled: bool = True
    web_search_enabled: bool = False
    web_search_result_count: int = Field(default=5, ge=1, le=10)
    web_search_timeout: float = Field(default=35, ge=10, le=60)
    text_per_hour: int = Field(default=120, ge=0, le=1200)
    text_per_day: int = Field(default=500, ge=0, le=10000)
    sticker_per_hour: int = Field(default=60, ge=0, le=1200)
    sticker_per_day: int = Field(default=200, ge=0, le=10000)
    vision_enabled: bool = True
    expected_wechat_version: Literal["4.1.0.16", "4.1.1.8"] = "4.1.0.16"

    @field_validator("allowed_chat_ids", "keywords", "trigger_only_chat_ids", "interest_keywords")
    @classmethod
    def clean_strings(cls, values):
        if any(not v.strip() or len(v) > 200 for v in values):
            raise ValueError("列表项必须为非空文本且不超过 200 字符")
        return list(dict.fromkeys(v.strip() for v in values))

    @field_validator("api_url")
    @classmethod
    def valid_api_url(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.fragment or parsed.query:
            raise ValueError("模型接口需使用不含内嵌凭据的 HTTPS 地址")
        return value.rstrip("/")


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

    def save(self, settings: Settings, api_key: str = ""):
        if settings.enabled and not settings.allowed_chat_ids:
            raise ValueError("先选择允许回复的会话")
        if settings.enabled and not (api_key.strip() or self.api_key()):
            raise ValueError("先配置模型 API Key")
        if api_key.strip():
            private_write(self.directory / "api-key.enc", self.fernet.encrypt(api_key.strip().encode()).decode())
        private_write(self.path, settings.model_dump_json(indent=2))
        jobs = os.environ.get("WHALEGIRL_UI_JOBS_DIR")
        if jobs and Path(jobs).is_dir():
            private_write(Path(jobs) / "authorized-chats.json", json.dumps({
                "account_id": settings.account_id, "allowed_chat_ids": settings.allowed_chat_ids}))
        self.settings = settings
        self.generation += 1

    def public(self):
        return {**self.settings.model_dump(), "api_key_configured": bool(self.api_key())}
