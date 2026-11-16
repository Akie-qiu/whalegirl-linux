from __future__ import annotations

import json
import hashlib
import hmac
import os
import secrets
import time
import threading
import re
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from .image_canvas import MAX_EDGE, cap_base_size
from .usage_pricing import ModelPricing

UNRESTRICTED_IMAGE_MAX_PER_DAY = 10


DEFAULT_BILIBILI_WATCH_TARGETS = (
    {'mid': '401742377', 'name': '原神', 'game': 'genshin', 'kind': 'official', 'enabled': True},
    {'mid': '1340190821', 'name': '崩坏：星穹铁道', 'game': 'starrail', 'kind': 'official', 'enabled': True},
    {'mid': '1636034895', 'name': '绝区零', 'game': 'zzz', 'kind': 'official', 'enabled': True},
)

DEFAULT_BILIBILI_WATCH_CALENDARS = {
    'genshin': {'version': '7.2', 'anchor_date': '2026-11-04', 'cycle_days': 42, 'preview_lead_days': 12},
    'starrail': {'version': '4.7', 'anchor_date': '2026-11-11', 'cycle_days': 42, 'preview_lead_days': 12},
    'zzz': {'version': '3.3', 'anchor_date': '2026-10-21', 'cycle_days': 42, 'preview_lead_days': 12},
}


def default_bilibili_watch_targets() -> list[dict]:
    return [dict(item) for item in DEFAULT_BILIBILI_WATCH_TARGETS]


def default_bilibili_watch_calendars() -> dict[str, dict]:
    return {game: dict(cfg) for game, cfg in DEFAULT_BILIBILI_WATCH_CALENDARS.items()}


class StickerChatPermission(BaseModel):
    model_config = ConfigDict(extra='forbid')
    auto_collect: bool = True
    command_collect: bool = True
    command_disable: bool = True


MODEL_PROFILE_IDS = ('flash', 'pro', 'responses', 'custom')
MODEL_PROFILE_NAMES = {'flash':'DeepSeek Flash', 'pro':'DeepSeek V4 Pro',
                       'responses':'OpenAI Responses', 'custom':'自定义接口'}
MODEL_CONNECTION_FIELDS = ('api_url','api_style','model','max_tokens','model_temperature',
                          'reasoning_effort','model_stream_enabled')


def valid_model_profile_id(value):
    return isinstance(value,str) and value != 'current' and re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',value) is not None


def validate_model_temperature(value, info):
    if value is not None:
        from .upstream.ai_engine import AIEngine
        if AIEngine._resolve_api_style(info.data.get('api_url', ''), info.data.get('api_style', 'auto')) == 'anthropic' and value > 1:
            raise ValueError('Anthropic Messages 接口的模型温度需为0～1')
    return value


def reply_temperature(settings, *, proactive=False):
    """An unset field preserves the existing ordinary/proactive defaults."""
    return (.65 if proactive else .55) if settings.model_temperature is None else settings.model_temperature


class ModelProfile(BaseModel):
    """Non-secret settings for one model card.

    API keys are deliberately kept in Config's encrypted profile-key file and
    never travel through the public config response.
    """
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default='', max_length=80)
    pricing: ModelPricing = Field(default_factory=ModelPricing)
    api_url: str = "https://api.deepseek.com/chat/completions"
    api_style: Literal['auto', 'openai', 'responses', 'anthropic'] = 'auto'
    model: str = Field(default="deepseek-chat", min_length=1, max_length=100)
    max_tokens: int = Field(default=220, ge=32, le=4000)
    model_temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: Literal['default', 'low', 'medium', 'high'] = 'default'
    model_stream_enabled: bool = False

    @field_validator('name')
    @classmethod
    def valid_name(cls,value):
        value=value.strip()
        if any(ord(c)<32 for c in value):raise ValueError('配置名称不能包含换行或控制字符')
        return value

    def connection_settings(self):
        return self.model_dump(include=set(MODEL_CONNECTION_FIELDS))

    @field_validator('model_temperature')
    @classmethod
    def valid_temperature(cls, value, info):
        return validate_model_temperature(value, info)

    @field_validator('api_url')
    @classmethod
    def valid_model_api_url(cls, value):
        value = value.strip()
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.fragment or parsed.query:
            raise ValueError('模型接口需使用不含内嵌凭据、查询参数或片段的 HTTP 或 HTTPS 地址')
        return value.rstrip('/')


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode='before')
    @classmethod
    def retired_growth_settings(cls,value):
        # Retired caps/sources remain loadable but do not control execution.
        if isinstance(value,dict):
            value=dict(value)
            value.pop('growth_daily_tokens',None)
            value.pop('growth_web_enabled',None)
            value.pop('bilibili_reads_per_day',None)
        return value

    @model_validator(mode='after')
    def valid_debounce_window(self):
        if 0 < self.debounce_max_seconds < max(self.debounce_single_seconds, self.debounce_burst_seconds):
            raise ValueError('消息合并总等待上限不能小于单条或连发等待；设为 0 可关闭合并')
        return self
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
    send_auto_recovery_enabled: bool = True
    safety_send_limits_enabled: bool = True
    safety_per_minute: int = Field(default=8, ge=0, le=60)
    safety_per_hour: int = Field(default=120, ge=0, le=1200)
    safety_chat_per_hour: int = Field(default=40, ge=0, le=600)
    safety_per_day: int = Field(default=500, ge=0, le=10000)
    safety_duplicate_seconds: int = Field(default=180, ge=30, le=1800)
    stickers_enabled: bool = True
    sticker_aggressiveness: Literal['low','normal','high','very_high','sticker_first'] = 'normal'
    sticker_usage_target: int = Field(default=0, ge=0, le=100)
    sticker_after_text: Literal['off','normal','high'] = 'normal'
    sticker_pair_probability: int = Field(default=55, ge=0, le=100)
    sticker_text_pair_eagerness: int = Field(default=55, ge=0, le=100)
    sticker_only_eagerness: int = Field(default=60, ge=0, le=100)
    sticker_reaction_eagerness: int = Field(default=50, ge=0, le=100)
    sticker_max_per_reply: int = Field(default=1, ge=1, le=3)
    sticker_style_note: str = Field(default='', max_length=1200)
    api_url: str = "https://api.deepseek.com/chat/completions"
    api_style: Literal['auto', 'openai', 'responses', 'anthropic'] = 'auto'
    reasoning_effort: Literal['default', 'low', 'medium', 'high'] = 'default'
    model_stream_enabled: bool = False
    model: str = Field(default="deepseek-chat", min_length=1, max_length=100)
    max_tokens: int = Field(default=220, ge=32, le=4000)
    model_temperature: float | None = Field(default=None, ge=0, le=2)
    @field_validator('model_temperature')
    @classmethod
    def valid_temperature(cls, value, info):
        return validate_model_temperature(value, info)

    history_pairs: int = Field(default=5, ge=0, le=15)
    min_send_interval: float = Field(default=6, ge=0, le=120)
    fast_reply_enabled: bool = True
    chat_cooldown: float = Field(default=15, ge=0, le=300)
    poll_interval: float = Field(default=1, ge=0.25, le=60)
    context_messages: int = Field(default=100, ge=10, le=100)
    fishtalk_enabled: bool = True
    fishtalk_auto_learn: bool = True
    fishtalk_reference_level: Literal['subtle', 'normal', 'strong'] = 'normal'
    fishtalk_check_mode: Literal['off', 'observe', 'light'] = 'light'
    fishtalk_sample_limit: int = Field(default=400, ge=40, le=1200)
    fishtalk_sample_days: int = Field(default=14, ge=1, le=90)
    fishtalk_excluded_sender_ids: list[str] = Field(default_factory=list, max_length=100)
    global_memory: bool = True
    memory_enabled: bool = True
    memory_consolidation_enabled: bool = True
    memory_token_budget: int = Field(default=1400, ge=400, le=6000)
    memory_item_limit: int = Field(default=8, ge=2, le=30)
    growth_enabled: bool = True
    life_autonomy_enabled: bool = True
    growth_mature_traits_chat_enabled: bool = False
    growth_tieba_enabled: bool = False
    growth_auto_apply: bool = True
    growth_bilibili_expression_shared: bool = True
    growth_tieba_expression_shared: bool = True
    growth_internet_model_profile: str = Field(default='flash', pattern=r'^[a-z][a-z0-9_-]{0,63}$')
    growth_reads_per_day: int = Field(default=3, ge=0, le=20)
    growth_interval_minutes: int = Field(default=180, ge=30, le=1440)
    growth_exploration: int = Field(default=25, ge=0, le=100)
    growth_interests: list[str] = Field(default_factory=list, max_length=30)
    bilibili_enabled: bool = False
    # Independent, opt-in ways to enqueue a specifically requested video read.
    # Bilibili mention polling can mark notifications as seen on the platform.
    bilibili_comment_mentions_enabled: bool = False
    bilibili_comment_reply_enabled: bool = False
    bilibili_reply_scope: Literal['all', 'whitelist'] = 'all'
    bilibili_reply_to_replies_enabled: bool = True
    bilibili_reply_allowed_sender_mids: list[str] = Field(default_factory=list, max_length=100)
    bilibili_reply_use_special_following: bool = True
    bilibili_reply_mode: Literal['auto', 'review'] = 'auto'
    bilibili_reply_deep_max_minutes: int = Field(default=30, ge=5, le=120)
    bilibili_reply_deep_max_windows: int = Field(default=6, ge=1, le=12)
    bilibili_reply_per_day: int = Field(default=12, ge=1, le=50)
    bilibili_reply_min_interval_seconds: int = Field(default=60, ge=15, le=3600)
    bilibili_reply_max_per_thread: int = Field(default=3, ge=1, le=10)
    bilibili_reply_max_age_hours: int = Field(default=24, ge=1, le=168)
    bilibili_reply_retention_limit: int = Field(default=20, ge=5, le=200)
    bilibili_reply_retention_days: int = Field(default=7, ge=1, le=90)
    bilibili_reply_record_ignored: bool = True
    bilibili_wechat_video_enabled: bool = False
    bilibili_interval_minutes: int = Field(default=120, ge=30, le=1440)
    bilibili_requests_per_day: int = Field(default=1200, ge=20, le=5000)
    bilibili_visual_enabled: bool = True
    bilibili_comments_enabled: bool = True
    bilibili_danmaku_enabled: bool = True
    bilibili_comments_limit: int = Field(default=60,ge=20,le=80)
    bilibili_comment_pages: int = Field(default=4,ge=1,le=5)
    bilibili_reply_threads: int = Field(default=3,ge=0,le=5)
    bilibili_danmaku_limit: int = Field(default=120,ge=24,le=200)
    bilibili_keyframes_limit: int = Field(default=16,ge=6,le=24)
    bilibili_segment_seconds: int = Field(default=300,ge=60,le=600)
    bilibili_media_enabled: bool = True
    bilibili_media_max_mb: int = Field(default=16,ge=4,le=32)
    bilibili_interest_seeds: list[str] = Field(default_factory=list, max_length=20)
    bilibili_excluded_topics: list[str] = Field(
        default_factory=lambda: ['恐怖', '克苏鲁', '鸣潮'],
        max_length=50,
    )
    bilibili_soft_preferences: str = Field(
        default='稍微多留意一点米哈游游戏圈（原神、崩坏：星穹铁道、绝区零、崩坏3）的玩家二创、整活切片与社区热梗吐槽；只是平时冲浪略微偏爱一点，全站其他好玩热梗照样正常看，不必每轮都挑游戏',
        max_length=300,
    )
    bilibili_feeds: list[Literal['recommend', 'popular', 'ranking', 'weekly', 'search', 'following', 'favorites', 'history']] = Field(
        default_factory=lambda: ['recommend', 'popular', 'ranking', 'weekly', 'search', 'following', 'favorites', 'history'], max_length=8)
    bilibili_memes_enabled: bool = True
    bilibili_memes_ttl_days: int = Field(default=30, ge=3, le=180)
    bilibili_meme_eagerness: int = Field(default=75, ge=0, le=100)
    bilibili_cache_mb: int = Field(default=64, ge=8, le=256)
    bilibili_cache_days: int = Field(default=7, ge=1, le=30)
    memory_cold_after_days: int = Field(default=30, ge=3, le=365)
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
    girlish_tone: Literal['off','natural','bright','soft'] = 'natural'
    chat_short_replies_enabled: bool = True
    chat_short_reply_sentences: int = Field(default=2, ge=1, le=5)
    chat_short_reply_chars: int = Field(default=48, ge=10, le=200)
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
    affection_private_style: Literal['warm','close','unreserved'] = 'close'
    private_unreserved_enabled: bool = False
    owner_ids: list[str] = Field(default_factory=list, max_length=20)
    intimate_friend_ids: list[str] = Field(default_factory=list, max_length=20)
    sticker_candidate_limit: int = Field(default=8, ge=4, le=20)
    sticker_cooldown_seconds: int = Field(default=75, ge=0, le=3600)
    pat_cooldown_seconds: int = Field(default=45, ge=10, le=600)
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
    autonomy_enabled: bool = False
    experience_growth_enabled: bool = True
    debounce_single_seconds: float = Field(default=0.65, ge=0, le=3)
    debounce_burst_seconds: float = Field(default=1.6, ge=0, le=5)
    debounce_max_seconds: float = Field(default=4, ge=0, le=8)
    followup_wait_enabled: bool = True
    followup_wait_seconds: float = Field(default=10, ge=3, le=10)
    reply_max_parts: int = Field(default=6, ge=1, le=6)
    quiet_default_minutes: int = Field(default=30, ge=1, le=1440)
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
    image_unrestricted_enabled: bool = False
    image_unrestricted_api_url: str = ''
    image_unrestricted_model: str = Field(default='', max_length=120)
    image_unrestricted_per_day: int = Field(default=2, ge=0, le=UNRESTRICTED_IMAGE_MAX_PER_DAY)
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
    bilibili_watch_enabled: bool = True
    bilibili_watch_chat_ids: list[str] = Field(default_factory=list, max_length=20)
    bilibili_watch_push_cooldown_minutes: int = Field(default=15, ge=0, le=720)
    bilibili_watch_spoiler_lock_minutes: int = Field(default=120, ge=15, le=1440)
    bilibili_watch_targets: list[dict] = Field(default_factory=default_bilibili_watch_targets, max_length=30)
    bilibili_watch_calendars: dict = Field(default_factory=default_bilibili_watch_calendars)
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
    expected_wechat_version: Literal["4.1.0.16", "4.1.1.8", "4.1.13.26"] = "4.1.0.16"

    @field_validator("allowed_chat_ids", "keywords", "trigger_only_chat_ids", "interest_keywords", "moments_topics", "moments_friend_ids",
                     "tieba_forums", "tieba_interests", "tieba_share_chat_ids", "growth_interests", "bilibili_interest_seeds",
                     "bilibili_excluded_topics", "bilibili_reply_allowed_sender_mids", "bilibili_watch_chat_ids", "proactive_chat_ids", "proactive_strict_chat_ids", "owner_ids", "intimate_friend_ids")
    @classmethod
    def clean_strings(cls, values):
        if any(not v.strip() or len(v) > 200 for v in values):
            raise ValueError("列表项必须为非空文本且不超过 200 字符")
        return list(dict.fromkeys(v.strip() for v in values))

    @field_validator("bilibili_reply_allowed_sender_mids")
    @classmethod
    def valid_bilibili_mids(cls, values):
        import re
        cleaned = []
        for raw in values:
            v = str(raw or '').strip()
            match = (
                re.fullmatch(r'([1-9][0-9]{0,19})(?:\s*(?:#|//|\(|（|:|：|\s).*)?', v)
                or re.fullmatch(r'[^-\d][^:：]{0,60}[:：]\s*([1-9][0-9]{0,19})(?:\s*(?:#|//|\(|（|\s).*)?', v)
            )
            if not match:
                raise ValueError("B站白名单 UID 必须为正整数数字编号")
            cleaned.append(match.group(1))
        return list(dict.fromkeys(cleaned))

    @field_validator("bilibili_watch_targets")
    @classmethod
    def valid_bilibili_watch_targets(cls, values):
        import re
        if not isinstance(values, list):
            raise ValueError("追更账号列表格式无效")
        cleaned = []
        seen = set()
        for raw in values:
            if not isinstance(raw, dict):
                raise ValueError("追更账号项必须为对象")
            raw_mid = str(raw.get("mid") or "").strip()
            match = re.fullmatch(r'([1-9][0-9]{0,19})', raw_mid)
            if not match:
                raise ValueError("追更账号 UID 必须为正整数")
            mid = match.group(1)
            if mid in seen:
                continue
            seen.add(mid)
            game = str(raw.get("game") or "all").strip()
            if game not in ("genshin", "starrail", "zzz", "all"):
                raise ValueError("追更游戏分类无效")
            kind = str(raw.get("kind") or "summary_up").strip()
            if kind not in ("official", "summary_up"):
                raise ValueError("追更账号类型无效")
            name = str(raw.get("name") or f"UID {mid}").replace("\n", " ").replace("\r", " ").strip()[:80]
            if not name:
                name = f"UID {mid}"
            cleaned.append({
                "mid": mid,
                "name": name,
                "game": game,
                "kind": kind,
                "enabled": bool(raw.get("enabled", True)),
            })
        return cleaned

    @field_validator("bilibili_watch_calendars")
    @classmethod
    def valid_bilibili_watch_calendars(cls, values):
        from datetime import date
        if not isinstance(values, dict):
            raise ValueError("三游版本日历格式无效")
        defaults = default_bilibili_watch_calendars()
        cleaned = {}
        for game, default_cfg in defaults.items():
            raw = values.get(game)
            if raw is None:
                cleaned[game] = dict(default_cfg)
                continue
            if not isinstance(raw, dict):
                raise ValueError(f"{game} 日历配置格式无效")
            anchor_str = str(raw.get("anchor_date") or default_cfg["anchor_date"]).strip()
            try:
                date.fromisoformat(anchor_str)
            except ValueError as exc:
                raise ValueError(f"{game} 基准日期需为 YYYY-MM-DD 格式") from exc
            try:
                cycle_days = int(raw.get("cycle_days", default_cfg["cycle_days"]))
                lead_days = int(raw.get("preview_lead_days", default_cfg["preview_lead_days"]))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{game} 周期天数需为整数") from exc
            if not (21 <= cycle_days <= 84):
                raise ValueError(f"{game} 版本周期需在 21～84 天之间")
            if not (5 <= lead_days <= 25):
                raise ValueError(f"{game} 前瞻提前天数需在 5～25 天之间")
            version = str(raw.get("version") or default_cfg["version"]).strip()[:24]
            if not version:
                version = default_cfg["version"]
            cleaned[game] = {
                "version": version,
                "anchor_date": anchor_str,
                "cycle_days": cycle_days,
                "preview_lead_days": lead_days,
            }
        return cleaned

    @field_validator("api_url", "image_api_url")
    @classmethod
    def valid_api_url(cls, value, info: ValidationInfo):
        value=value.strip()
        parsed = urlsplit(value)
        schemes=('http','https') if info.field_name=='api_url' else ('https',)
        if parsed.scheme not in schemes or not parsed.hostname or parsed.username is not None or parsed.fragment or parsed.query:
            protocol='HTTP 或 HTTPS' if info.field_name=='api_url' else 'HTTPS'
            raise ValueError('模型接口需使用不含内嵌凭据、查询参数或片段的 '+protocol+' 地址')
        return value.rstrip("/")

    @field_validator("image_unrestricted_api_url")
    @classmethod
    def valid_unrestricted_image_api_url(cls, value):
        value = (value or '').strip()
        if not value:
            return ''
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.fragment or parsed.query:
            raise ValueError('无限制生图接口需使用不含内嵌凭据、查询参数或片段的 HTTP 或 HTTPS 地址')
        return value.rstrip('/')

    @field_validator("image_unrestricted_model")
    @classmethod
    def clean_unrestricted_image_model(cls, value):
        return (value or '').strip()

    @field_validator('image_size')
    @classmethod
    def cap_image_size(cls, value):
        capped = cap_base_size(value)
        if capped != value:
            raise ValueError(f'图片基础分辨率最长边不得超过 {MAX_EDGE}px')
        return value

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
        self.lock=threading.RLock()
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "settings.json"
        if self.path.exists():
            saved = json.loads(self.path.read_text("utf-8"))
            # Accept old contradictory windows in memory, without silently
            # rewriting settings or making an existing install unbootable.
            if isinstance(saved, dict):
                cap = saved.get('debounce_max_seconds', 4)
                single = saved.get('debounce_single_seconds', .65)
                burst = saved.get('debounce_burst_seconds', 1.6)
                if all(isinstance(v, (int, float)) for v in (cap, single, burst)) and 0 < cap < max(single, burst):
                    saved['debounce_max_seconds'] = max(single, burst)
            # Keep legacy settings within the configurable context window.
            if isinstance(saved, dict) and isinstance(saved.get("context_messages"), int):
                saved["context_messages"] = max(10, min(100, saved["context_messages"]))
            if isinstance(saved, dict) and isinstance(saved.get('image_size'), str):
                previous_image_size = saved['image_size']
                saved['image_size'] = cap_base_size(previous_image_size)
                if saved['image_size'] != previous_image_size:
                    private_write(self.path, json.dumps(saved, ensure_ascii=False))
            self.active_model_profile = (saved.get('active_model_profile') if isinstance(saved, dict) else '') or self._infer_profile_id(saved)
            if not valid_model_profile_id(self.active_model_profile):
                self.active_model_profile = 'custom'
            if isinstance(saved, dict):
                saved.pop('active_model_profile', None)
            self.settings = Settings.model_validate(saved)
        else:
            self.settings = Settings()
            self.active_model_profile = 'custom'
        self.admin_token = self._secret("admin.token", lambda: secrets.token_urlsafe(32))
        self.gateway_token = self._secret("gateway.token", lambda: secrets.token_hex(32))
        self.fernet = Fernet(self._secret("encryption.key", lambda: Fernet.generate_key().decode()).encode())
        self._model_profiles_path = directory / 'model-profiles.json'
        self._model_keys_path = directory / 'model-api-keys.enc'
        self._model_profiles = {}
        self._load_model_profiles()
        self.generation = 0
        self.console_sessions = {}

    @staticmethod
    def _infer_profile_id(values):
        url = str(values.get('api_url') or '').rstrip('/')
        style = values.get('api_style') or 'auto'
        model = str(values.get('model') or '')
        if model == 'deepseek-flash' and url == 'https://api.deepseek.com/chat/completions':
            return 'flash'
        if model == 'deepseek-v4-pro' and url == 'https://api.deepseek.com/chat/completions':
            return 'pro'
        if style == 'responses' or url.endswith('/responses'):
            return 'responses'
        return 'custom'

    def _legacy_api_key(self):
        path = self.directory / 'api-key.enc'
        if not path.exists():
            return ''
        try:
            return self.fernet.decrypt(path.read_bytes()).decode()
        except Exception:
            return ''

    def _profile_from_settings(self, settings: Settings):
        return ModelProfile.model_validate({
            'api_url': settings.api_url,
            'api_style': settings.api_style,
            'model': settings.model,
            'max_tokens': settings.max_tokens,
            'model_temperature': settings.model_temperature,
            'reasoning_effort': settings.reasoning_effort,
            'model_stream_enabled': settings.model_stream_enabled,
        })

    def _default_model_profiles(self):
        current = self._profile_from_settings(self.settings)
        profiles = {
            'flash': ModelProfile(api_url='https://api.deepseek.com/chat/completions', api_style='openai',
                                  model='deepseek-flash'),
            'pro': ModelProfile(api_url='https://api.deepseek.com/chat/completions', api_style='openai',
                                model='deepseek-v4-pro'),
            'responses': ModelProfile(api_url='https://api.deepseek.com/responses', api_style='responses',
                                      model=current.model),
            'custom': current,
        }
        profiles[self.active_model_profile] = current
        return profiles

    def _load_model_keys(self):
        if not self._model_keys_path.exists():
            return {}
        try:
            value = json.loads(self.fernet.decrypt(self._model_keys_path.read_bytes()).decode())
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _save_model_keys(self, values):
        private_write(self._model_keys_path, self.fernet.encrypt(json.dumps(values, ensure_ascii=False).encode()).decode())

    def _write_model_profiles(self):
        private_write(self._model_profiles_path, json.dumps({
            'version': 2,
            'active': self.active_model_profile,
            # Original releases can still read the four legacy connection
            # records. New presentation/pricing metadata stays separate.
            'profiles': {key: value.connection_settings() for key, value in self._model_profiles.items()},
            'metadata': {key: {'name':value.name,'pricing':value.pricing.model_dump()}
                         for key,value in self._model_profiles.items()},
        }, ensure_ascii=False, indent=2))

    def _load_model_profiles(self):
        defaults = self._default_model_profiles()
        migrated = False
        if self._model_profiles_path.exists():
            try:
                raw = json.loads(self._model_profiles_path.read_text('utf-8'))
                source = raw.get('profiles') if isinstance(raw, dict) and isinstance(raw.get('profiles'), dict) else raw
                metadata = raw.get('metadata') if isinstance(raw,dict) and isinstance(raw.get('metadata'),dict) else {}
                if isinstance(raw, dict) and valid_model_profile_id(raw.get('active')):
                    self.active_model_profile = raw['active']
                for key,value in source.items() if isinstance(source,dict) else ():
                    if valid_model_profile_id(key) and isinstance(value,dict):
                        meta=metadata.get(key) if isinstance(metadata.get(key),dict) else {}
                        defaults[key] = ModelProfile.model_validate({**value,**meta})
            except (OSError, ValueError, TypeError):
                migrated = True
        else:
            migrated = True
        self._model_profiles = defaults
        keys = self._load_model_keys()
        active = self.active_model_profile
        if not self._model_keys_path.exists():
            legacy = self._legacy_api_key()
            if legacy:
                keys[active] = legacy
                self._save_model_keys(keys)
        # The first load upgrades the single legacy model setting to isolated
        # cards without changing the active runtime configuration.
        if migrated:
            self._write_model_profiles()
        if self.active_model_profile not in self._model_profiles:
            self.active_model_profile = 'custom'

    def model_profiles_public(self):
        keys = self._load_model_keys()
        return {
            key: {**profile.model_dump(), 'name':profile.name or MODEL_PROFILE_NAMES.get(key) or profile.model,
                  'api_key_configured': bool(keys.get(key))}
            for key, profile in self._model_profiles.items()
        }

    def model_profile(self, profile_id):
        """A copy of a saved card, without activating it or reading its secret."""
        if profile_id not in self._model_profiles:
            raise ValueError('模型卡片不存在')
        return self._model_profiles[profile_id].model_copy(deep=True)

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

    def api_key(self, profile_id: str | None = None):
        profile_id = profile_id or self.active_model_profile
        keys = self._load_model_keys()
        value = str(keys.get(profile_id) or '').strip()
        if value:
            return value
        # Legacy migration runs once at startup. An unconfigured card must
        # never inherit another card's legacy key, including after a restart.
        return ''

    def save_model_profile(self, profile_id: str, profile: ModelProfile, api_key: str = '', activate: bool = True, *, create=False):
        if not valid_model_profile_id(profile_id) or (profile_id not in self._model_profiles and not create):
            raise ValueError('模型卡片不存在')
        profile = ModelProfile.model_validate(profile).model_copy(deep=True)
        supplied_key = api_key.strip()
        existing_key = self.api_key(profile_id)
        if activate and self.settings.enabled and not (supplied_key or existing_key):
            raise ValueError('启用自动回复前，请先为当前模型卡片配置 API Key')
        self._model_profiles[profile_id] = profile
        if supplied_key:
            keys = self._load_model_keys()
            keys[profile_id] = supplied_key
            self._save_model_keys(keys)
        if activate:
            self.active_model_profile = profile_id
        self._write_model_profiles()
        if activate:
            updated = self.settings.model_copy(update=profile.connection_settings())
            self.save(updated, profile_id=profile_id)
        return self.public()

    def create_model_profile(self, profile: ModelProfile, api_key: str = '', *, activate=False):
        identity='model-'+uuid.uuid4().hex[:16]
        return identity,self.save_model_profile(identity,profile,api_key,activate,create=True)

    def pricing_for_request(self, *, api_url, model, api_key='', profile_id=None):
        """Freeze the selected card's non-secret rates when an engine is built."""
        from .upstream.ai_engine import AIEngine
        def matches(identity):
            p=self._model_profiles[identity]
            return (p.model==model and
                AIEngine._normalize_api_url(p.api_url,AIEngine._resolve_api_style(p.api_url,p.api_style)) == api_url and
                (not api_key or hmac.compare_digest(self.api_key(identity).encode(),api_key.encode())))
        if profile_id and profile_id in self._model_profiles and matches(profile_id):
            choices=[profile_id]
        elif self.active_model_profile in self._model_profiles and matches(self.active_model_profile):
            choices=[self.active_model_profile]
        else:
            choices=[identity for identity in self._model_profiles if matches(identity)]
        if len(choices)!=1:return {}
        identity=choices[0];p=self._model_profiles[identity]
        return {'profile_id':identity,'name':p.name or MODEL_PROFILE_NAMES.get(identity) or p.model,
                'pricing':p.pricing.model_dump()}

    def image_api_key(self):
        path = self.directory / 'image-api-key.enc'
        return self.fernet.decrypt(path.read_bytes()).decode() if path.exists() else ''

    def save_image_key(self, key):
        if key.strip():
            private_write(self.directory / 'image-api-key.enc', self.fernet.encrypt(key.strip().encode()).decode())

    def unrestricted_image_api_key(self):
        path = self.directory / 'unrestricted-image-api-key.enc'
        return self.fernet.decrypt(path.read_bytes()).decode() if path.exists() else ''

    def save_unrestricted_image_key(self, key):
        if key.strip():
            private_write(self.directory / 'unrestricted-image-api-key.enc', self.fernet.encrypt(key.strip().encode()).decode())

    def search_api_key(self):
        path = self.directory / 'search-api-key.enc'
        return self.fernet.decrypt(path.read_bytes()).decode() if path.exists() else ''

    def save_search_key(self, key):
        if key.strip():
            private_write(self.directory / 'search-api-key.enc', self.fernet.encrypt(key.strip().encode()).decode())

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

    def save(self, settings: Settings, api_key: str = "", *, profile_id: str | None = None):
        with self.lock:
            return self._save(settings,api_key,profile_id=profile_id)

    def _save(self, settings: Settings, api_key: str = "", *, profile_id: str | None = None):
        settings = Settings.model_validate(settings.model_dump())
        image_size = cap_base_size(settings.image_size)
        if image_size != settings.image_size:
            settings = settings.model_copy(update={'image_size': image_size})
        if settings.enabled and not settings.allowed_chat_ids:
            raise ValueError("先选择允许回复的会话")
        profile_id = profile_id or self.active_model_profile
        if profile_id not in self._model_profiles:
            raise ValueError('模型卡片不存在')
        worker=settings.growth_internet_model_profile
        if worker!='current' and worker not in self._model_profiles:
            raise ValueError('自主学习所用的模型配置不存在')
        if settings.enabled and not (api_key.strip() or self.api_key(profile_id)):
            raise ValueError("先配置模型 API Key")
        if api_key.strip():
            keys = self._load_model_keys()
            keys[profile_id] = api_key.strip()
            self._save_model_keys(keys)
            private_write(self.directory / "api-key.enc", self.fernet.encrypt(api_key.strip().encode()).decode())
        self.active_model_profile = profile_id
        profile=self._profile_from_settings(settings)
        previous=self._model_profiles[profile_id]
        self._model_profiles[profile_id]=profile.model_copy(update={'name':previous.name,'pricing':previous.pricing})
        self._write_model_profiles()
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
        return {**self.settings.model_dump(), 'active_model_profile': self.active_model_profile,
                'model_profiles': self.model_profiles_public(),
                "api_key_configured": bool(self.api_key()),
                "image_api_key_configured": bool(self.image_api_key()),
                "image_unrestricted_api_key_configured": bool(self.unrestricted_image_api_key()),
                "search_api_key_configured": bool(self.search_api_key())}
