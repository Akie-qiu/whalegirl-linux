"""Versioned official list-price references, never a provider invoice or a budget.

No network access on the reply path. Unknown model names are deliberately not
fuzzy-matched. Cache reads and reasoning are subsets, not additional tokens.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelPricing(BaseModel):
    """A saved model's rates per million tokens, without currency conversion."""
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    mode: Literal['official', 'custom', 'none'] = 'official'
    currency: Literal['CNY', 'USD'] = 'CNY'
    input: float | None = Field(default=None, ge=0, le=1_000_000_000)
    cached: float | None = Field(default=None, ge=0, le=1_000_000_000)
    output: float | None = Field(default=None, ge=0, le=1_000_000_000)
    write: float | None = Field(default=None, ge=0, le=1_000_000_000)

    @model_validator(mode='after')
    def required_custom_rates(self):
        if self.mode == 'custom' and (self.input is None or self.output is None):
            raise ValueError('自定义价格需填写输入与输出单价；免费项目可明确填0')
        return self

TZ = timezone(timedelta(hours=8))
VERSION = 'official-20261110-v1'
VERIFIED = '2026-11-10'
DEEPSEEK = 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'
GOOGLE = 'https://ai.google.dev/gemini-api/docs/pricing/'
ANTHROPIC = 'https://platform.claude.com/docs/en/about-claude/pricing'
HOLIDAY_SOURCE = 'https://www.gov.cn/zhengce/content/202511/content_7047090.htm'
HOLIDAYS_2026 = [('01-01', '01-03'), ('02-15', '02-23'), ('04-04', '04-06'),
                 ('05-01', '05-05'), ('06-19', '06-21'), ('09-25', '09-27'), ('10-01', '10-07')]
RELAY_ALIASES = {'deepseek-v4.1-flash': 'deepseek-flash',
                 'deepseek-v4.1-flash-1017': 'deepseek-flash'}

# Values are per million tokens. DeepSeek values here are OFF-PEAK CNY.
CATALOG = [
    dict(models=['deepseek-flash', 'deepseek-v4-flash', 'deepseek-v4-flash-vision-exp'],
         vendor='DeepSeek', currency='CNY', input='1', cached='.02', output='4', source=DEEPSEEK, peak=True),
    dict(models=['deepseek-v4-pro'], vendor='DeepSeek', currency='CNY', input='4.5',
         cached='.15', output='13.5', source=DEEPSEEK, peak=True),
    dict(models=['gemini-3.8-flash'], vendor='Google', currency='USD', input='.75',
         cached='.075', output='3.75', source=GOOGLE, promo_until='2026-12-31', after_multiplier=2),
    dict(models=['gemini-3.8-flash-tts'], vendor='Google', currency='USD', input='.5',
         cached='.5', output='9', source=GOOGLE, promo_until='2026-12-31', after_multiplier=2,
         audio_output=True,
         note='仅上报的文本输入/音频输出Token，不含中转按秒费用'),
    dict(models=['claude-sonnet-4-6', 'claude-sonnet-4-5'], vendor='Anthropic', currency='USD',
         input='3', cached='.3', output='15', write='3.75', write_long='6', source=ANTHROPIC),
    dict(models=['claude-opus-4-6', 'claude-opus-4-5', 'claude-opus-4-7', 'claude-opus-4-8'],
         vendor='Anthropic', currency='USD', input='5', cached='.5', output='25',
         write='6.25', write_long='10', source=ANTHROPIC),
    dict(models=['claude-haiku-4-5'], vendor='Anthropic', currency='USD', input='1', cached='.1',
         output='5', write='1.25', write_long='2', source=ANTHROPIC),
    dict(models=['claude-sonnet-5-5'], vendor='Anthropic', currency='USD', input='2', cached='.1',
         output='10', write='2.5', write_long='4', source=ANTHROPIC),
]


def catalogue():
    return {'version': VERSION, 'verified_on': VERIFIED, 'unit': 'per_1M_tokens',
            'rates': CATALOG, 'relay_aliases': RELAY_ALIASES, 'holiday_source': HOLIDAY_SOURCE,
            'notice': 'DeepSeek中转按管理员确认的官方同价规则核算；不同币种不合并；未核验模型、额外工具和存储费用不猜测。'}


def _decimal(value):
    return Decimal(str(value))


def _number(value):
    return round(float(value), 10)


def quote(event, *, historical=False):
    model = str(event.get('model') or '').casefold()
    base = {'version': VERSION, 'verified_on': VERIFIED,
            'unit': 'per_1M_tokens',
            'basis': 'current_price_recalculation' if historical else 'request_price_snapshot',
            'status': 'unknown', 'reason': '', 'currency': '', 'low': None, 'high': None}
    provider = str(event.get('provider') or '').casefold()
    op = event.get('operation')
    # Official search/fetch is priced by successful request, not by tokens.
    if op in ('search', 'fetch', 'growth_search', 'growth_fetch'):
        if provider != 'api.deepseek.com':
            return {**base, 'reason': '未核验该搜索服务的按次价格'}
        value = .005 if event.get('status') == 'success' else 0
        return {**base, 'status': 'reference', 'currency': 'CNY', 'source': DEEPSEEK,
                'low': value, 'high': value, 'reason': 'DeepSeek官方每千次成功请求5元'}
    saved = event.get('pricing_snapshot') or {}
    pricing = ModelPricing.model_validate(saved.get('pricing') or {})
    custom = pricing.mode == 'custom'
    if custom:base.update(version='custom-profile-v1',verified_on=None)
    base.update(pricing_mode=pricing.mode)
    if saved.get('profile_id'):
        base.update(profile_id=str(saved['profile_id'])[:64], profile_name=str(saved.get('name') or '')[:80])
    if pricing.mode == 'none':
        return {**base, 'reason': '该模型配置未启用费用估算，保留Token用量'}
    canonical = RELAY_ALIASES.get(model, model)
    rate = ({'vendor':'管理员配置', 'currency':pricing.currency, 'source':'administrator',
        'input':pricing.input, 'output':pricing.output,
        'cached':pricing.cached if pricing.cached is not None else pricing.input,
        'write':pricing.write if pricing.write is not None else pricing.input,
        'write_long':pricing.write if pricing.write is not None else pricing.input}
        if custom else next((r for r in CATALOG if canonical in r['models']), None))
    if rate is None:
        return {**base, 'reason': '模型名未匹配已核验的官方价格，不猜测别名或中转价格'}
    base.update(currency=rate['currency'], source=rate['source'], vendor=rate['vendor'])
    input_count, output_count = event.get('input_tokens'), event.get('output_tokens')
    if not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in (input_count, output_count)):
        return {**base, 'reason': '缺少完整输入或输出Token回执'}
    if rate.get('audio_output') and output_count==0:
        return {**base,'reason':'音频输出Token未被有效计量，可能按秒收费，不能由0Token推断免费'}
    cached, write = event.get('cached_tokens'), event.get('cache_write_tokens') or 0
    if (not isinstance(write, int) or isinstance(write, bool) or write < 0
            or cached is not None and (not isinstance(cached, int) or isinstance(cached, bool)
                                      or cached < 0 or cached + write > input_count)):
        return {**base, 'reason': '缓存字段矛盾，未计算费用'}
    if write and rate.get('write') is None:
        return {**base, 'reason': '该回执含未核验计价的缓存写入'}
    miss = event.get('uncached_tokens')
    if miss is not None and (not isinstance(miss,int) or isinstance(miss,bool) or miss<0 or miss+write>input_count
                            or cached is not None and miss+cached+write!=input_count):
        return {**base, 'reason': '缓存命中与未命中计数矛盾，未计算费用'}
    created = datetime.fromtimestamp(event.get('created') or datetime.now(TZ).timestamp(), TZ)
    multiplier_low = multiplier_high = 1
    reasons = []
    if custom:
        reasons.append('按此模型配置保存的自定义单价估算')
    elif canonical != model:
        reasons.append('DeepSeek中转别名按官方Flash版本及管理员确认的同价规则核算')
    if rate.get('peak') and created.weekday() < 5 and (9 <= created.hour < 12 or 14 <= created.hour < 18):
        if created.year == 2026:
            holiday = any(a <= created.strftime('%m-%d') <= b for a, b in HOLIDAYS_2026)
            multiplier_low = multiplier_high = 1 if holiday else 2
        else:
            multiplier_high = 2
            reasons.append('该年份节假日历未核验，按空闲/高峰范围展示')
    if rate.get('promo_until') and created.date().isoformat() > rate['promo_until']:
        multiplier_low *= rate['after_multiplier']; multiplier_high *= rate['after_multiplier']
    i, c, o = (_decimal(rate[k]) for k in ('input', 'cached', 'output'))
    if cached is None:
        miss = event.get('uncached_tokens')
        if isinstance(miss, int) and not isinstance(miss, bool) and 0 <= miss <= input_count - write:
            cached = input_count - write - miss
        else:
            reasons.append('缓存命中未上报，展示可能费用范围')
    if cached is None:
        low_input, high_input = (input_count-write)*min(c, i), (input_count-write)*max(c, i)
    else:
        low_input = high_input = (input_count-cached-write)*i + cached*c
    low = low_input + output_count*o
    high = high_input + output_count*o
    if write:
        low += write*_decimal(rate['write'])
        high += write*_decimal(rate['write_long'])
        if not custom:reasons.append('缓存写入未保留有效期，按5分钟至1小时范围展示')
    low = low * multiplier_low / 1_000_000
    high = high * multiplier_high / 1_000_000
    if event.get('estimated_fields'):
        reasons.append('Token含估算值')
    if custom:
        if pricing.cached is None:reasons.append('缓存命中未单独定价，沿用输入单价')
        if write and pricing.write is None:reasons.append('缓存写入未单独定价，沿用输入单价')
    elif rate['vendor']=='DeepSeek' and provider!='api.deepseek.com':
        reasons.append('管理员确认DeepSeek中转与官方同价')
    elif provider not in ('api.deepseek.com', 'api.anthropic.com', 'generativelanguage.googleapis.com'):
        reasons.append('第三方中转，仅按同名模型官方价参考')
    return {**base, 'status': 'range' if low != high else 'reference', 'low': _number(low), 'high': _number(high),
            'reason': '；'.join(reasons) or '按接口Token回执及官方标准价估算',
            'rates': {k: rate.get(k) for k in ('input', 'cached', 'output', 'write')},
            'token_estimated': bool(event.get('estimated_fields')), 'peak_multiplier': [multiplier_low, multiplier_high]}
