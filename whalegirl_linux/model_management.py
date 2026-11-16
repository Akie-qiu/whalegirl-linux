"""On-demand model discovery and secret-safe model editor requests."""
from datetime import datetime, timezone
import json
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .settings import ModelProfile, valid_model_profile_id


class ModelDraftRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profile_id: str | None = None
    profile: ModelProfile
    api_key: str = Field(default='', max_length=1024, repr=False)

    @field_validator('profile_id')
    @classmethod
    def valid_identity(cls,value):
        if value is not None and not valid_model_profile_id(value):raise ValueError('模型配置标识无效')
        return value


class ModelCreateRequest(ModelDraftRequest):
    activate: bool = False
    copy_from: str | None = None
    copy_key: bool = False

    @field_validator('copy_from')
    @classmethod
    def valid_source(cls,value):
        return ModelDraftRequest.valid_identity(value)


class ModelListRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profile_id: str | None = None
    api_url: str = Field(min_length=1,max_length=2048)
    api_style: str = Field(default='auto', pattern='^(auto|openai|responses|anthropic)$')
    api_key: str = Field(default='',max_length=1024,repr=False)

    @field_validator('api_url')
    @classmethod
    def valid_url(cls,value):return ModelProfile.valid_model_api_url(value)

    @field_validator('profile_id')
    @classmethod
    def valid_identity(cls,value):return ModelDraftRequest.valid_identity(value)


def catalog_endpoint(api_url, api_style='auto'):
    from .upstream.ai_engine import AIEngine
    style=AIEngine._resolve_api_style(api_url,api_style)
    url=AIEngine._normalize_api_url(ModelProfile.valid_model_api_url(api_url),style)
    parsed=urlsplit(url);path=parsed.path.rstrip('/')
    for suffix in ('/chat/completions','/responses','/messages','/completions'):
        if path.endswith(suffix):path=path[:-len(suffix)];break
    return urlunsplit((parsed.scheme,parsed.netloc,path+'/models','',''))


def request_key(config,profile_id,api_url,api_style,supplied=''):
    if supplied.strip():return supplied.strip()
    if not profile_id:return ''
    saved=config.model_profile(profile_id)
    if catalog_endpoint(saved.api_url,saved.api_style)!=catalog_endpoint(api_url,api_style):
        raise ValueError('接口地址已变化，请填写该地址的API Key后再操作；已保存的密钥仍保留在原配置')
    return config.api_key(profile_id)


async def fetch_models(api_url,api_style,key,*,client=None):
    from .upstream.ai_engine import AIEngine
    endpoint=catalog_endpoint(api_url,api_style)
    style=AIEngine._resolve_api_style(api_url,api_style)
    headers={'Accept':'application/json'}
    if key:
        if style=='anthropic':headers.update({'x-api-key':key,'anthropic-version':'2023-06-01'})
        else:headers['Authorization']='Bearer '+key
    owned=client is None
    client=client or httpx.AsyncClient(timeout=12,trust_env=False,follow_redirects=False)
    found={};cursor=None;partial=False
    try:
        for _ in range(3):
            params=({'limit':100,'after_id':cursor} if cursor else {'limit':100}) if style=='anthropic' else None
            async with client.stream('GET',endpoint,headers=headers,params=params,follow_redirects=False) as response:
                if response.status_code!=200:
                    messages={401:'密钥无效或没有读取模型列表的权限',403:'接口拒绝读取模型列表，请核对密钥分组',
                        404:'此地址不提供模型列表，可检查地址或手动填写模型名称',429:'接口请求过于频繁，请稍后再试'}
                    raise ValueError(messages.get(response.status_code,
                        '模型列表读取失败（HTTP '+str(response.status_code)+'），可手动填写模型名称'))
                raw=bytearray()
                async for part in response.aiter_bytes():
                    raw.extend(part)
                    if len(raw)>2*1024*1024:raise ValueError('模型列表过大，请手动填写模型名称')
            try:body=json.loads(raw)
            except (ValueError,UnicodeError):raise ValueError('接口没有返回JSON模型列表，可手动填写模型名称') from None
            rows=body.get('data',body.get('models')) if isinstance(body,dict) else body
            if not isinstance(rows,list):raise ValueError('接口返回的模型列表格式不兼容，可手动填写模型名称')
            for row in rows:
                identity=row.get('id',row.get('name')) if isinstance(row,dict) else row
                if not isinstance(identity,str) or not identity.strip() or len(identity)>100 or any(ord(c)<32 for c in identity):continue
                identity=identity.strip();found[identity]={'id':identity}
                if len(found)>=2000:partial=True;break
            more=isinstance(body,dict) and body.get('has_more') is True
            if not more:
                partial=False;break
            if len(found)>=2000:break
            next_cursor=body.get('last_id')
            if style!='anthropic' or not isinstance(next_cursor,str) or not next_cursor or next_cursor==cursor:
                partial=True;break
            cursor=next_cursor;partial=True
        else:partial=True
        if not found:raise ValueError('接口未返回可用模型名称，仍可手动填写')
        return {'models':sorted(found.values(),key=lambda m:m['id'].casefold()),'count':len(found),
            'partial':partial,'endpoint':endpoint,'fetched_at':datetime.now(timezone.utc).timestamp()}
    except httpx.HTTPError as exc:
        label='请求超时' if isinstance(exc,httpx.TimeoutException) else '网络连接失败'
        raise ValueError('模型列表'+label+'，请检查地址或稍后重试；也可手动填写') from None
    finally:
        if owned:await client.aclose()
