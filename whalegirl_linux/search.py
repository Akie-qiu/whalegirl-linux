"""DeepSeek Search API; search evidence stays separate from chat style."""
from datetime import datetime, timedelta, timezone
import re
import time
from urllib.parse import urlsplit

import httpx


def strip_search_citations(text):
    text=str(text or '')
    # Official native search format, including multiple non-contiguous result IDs.
    text=re.sub(r'%cite%.*?%/cite%','',text,flags=re.S)
    text=re.sub(r'%cite%[^\n]*$','',text)
    text=text.replace('%noresults','')
    text=re.sub(r'cite[^]*','',text)
    text=re.sub(r'\[([^\]\n]+)\]\(https?://[^)\s]+\)',r'\1',text)
    # Citation-style numeric suffixes, not standalone arrays or ordinary numbering.
    text=re.sub(r'(?<=[\w\u3400-\u9fff。！？.!?）)])(?:\s*\[\d+(?:\s*[,，、-]\s*\d+)*\])+','',text)
    text=re.sub(r'【\d+(?:\s*[,，、-]\s*\d+)*】','',text)
    text=re.sub(r'(?im)\n\s*(?:参考资料|参考来源|参考链接|信息来源|Sources|References)\s*[:：].*\Z','',text,flags=re.S)
    return text.strip()


def public_query(value):
    query=re.sub(r'\s+',' ',str(value or '')).strip()[:128]
    if not query or re.search(r'wxid_[\w-]+|\d+@chatroom|sk-[A-Za-z0-9_-]{10,}',query):
        return ''
    return query


def search_prompt(settings):
    if not settings.web_search_enabled:return '\n联网搜索当前关闭，不要声称刚刚查过网上资料。'
    date=datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    return ('\n【按需联网】当前北京时间 '+date+'。普通闲聊、接梗和群友记忆不需要联网。'
        '用户明确要求联网/搜索/查最新时，必须先在search_query填写最短公共检索词，actions暂留空，收到工具结果后再回答；不要只说我去搜一下。'
        '回答确实依赖近期事实、天气或时效信息时，也可主动检索。需要读取用户给出的公开网页时填写fetch_url；两者每轮只选一个。'
        '问题可直接可靠回答时search_query留空，不要为了闲聊强行搜索；不确定但确有查证价值时可以用。'
        '搜索词只含公共信息，不发送群友姓名、微信ID、私聊原文、密码或整段聊天。群内旧事用记忆工具，不用网页。'
        '检索结果回传后再回答；没有成功结果不能声称已联网或把猜测当成最新事实。'
        '联网后的回复仍是普通口语聊天，不展示引用编号、%cite%标记、来源列表或Markdown链接，也不要写检索过程；只在用户明确索要网址时给网址。')


class DeepSeekSearch:
    def __init__(self):
        self.last={}

    async def run(self, engine, query, settings):
        started=time.monotonic();query=public_query(query)
        result={'ok':False,'text':'','requests':0,'error':''}
        try:
            if not settings.web_search_enabled:
                result['error']='联网搜索已关闭';return result
            if not query:
                result['error']='没有合适的公共检索词';return result
            if engine.api_style!='openai' or urlsplit(engine.api_url).hostname!='api.deepseek.com':
                result['error']='当前接口不是 DeepSeek 官方联网搜索接口';return result
            payload={'query':query,'count':settings.web_search_result_count,'need_content':True}
            client=await engine._get_client()
            response=await client.post('https://api.deepseek.com/v1/tools/search',json=payload,timeout=httpx.Timeout(settings.web_search_timeout))
            response.raise_for_status();data=response.json()
            rows=data.get('results')
            if not isinstance(rows,list):raise ValueError('Missing search results')
            result['requests']=1
            documents=[]
            for row in rows[:settings.web_search_result_count]:
                if not isinstance(row,dict):continue
                documents.append({'title':str(row.get('title') or '')[:250],
                    'url':str(row.get('url') or '')[:1500],
                    'date':row.get('date'),'site':row.get('site_name'),
                    'summary':str(row.get('summary') or row.get('snippet') or '')[:1800],
                    'content':str(row.get('content') or '')[:3500]})
            result['result_count']=len(documents)
            if not documents:result['error']='本次搜索没有匹配结果'
            else:
                result.update(ok=True,documents=documents)
                result['text']='\n\n'.join((d['title']+'\n日期：'+str(d['date'] or '未标注')+'\n'+d['summary']+'\n'+d['content']) for d in documents)[:18000]
        except httpx.TimeoutException:
            result['error']='联网搜索超时'
        except httpx.HTTPStatusError as exc:
            result['error']='联网接口 HTTP '+str(exc.response.status_code)
        except (ValueError,TypeError,KeyError,IndexError):
            result['error']='联网返回格式暂不可用'
        except httpx.HTTPError:
            result['error']='联网接口暂时无法连接'
        finally:
            result['seconds']=round(time.monotonic()-started,3)
            result['checked_at']=time.time()
            self.last={k:v for k,v in result.items() if k not in ('text','documents')}
        return result

    async def fetch(self,engine,url,settings):
        started=time.monotonic();result={'ok':False,'text':'','requests':0,'error':'','operation':'fetch'}
        try:
            parsed=urlsplit(str(url or ''))
            host=parsed.hostname or ''
            if (not settings.web_search_enabled or parsed.scheme not in ('http','https') or not host or
                    parsed.username or parsed.password or len(url)>2048):
                result['error']='没有可读取的公开网页地址';return result
            import ipaddress
            try:public_ip=ipaddress.ip_address(host).is_global
            except ValueError:public_ip=True
            if not public_ip or host in ('localhost','localhost.localdomain') or host.endswith(('.local','.internal')):
                result['error']='不抓取本机或内网网页';return result
            if engine.api_style!='openai' or urlsplit(engine.api_url).hostname!='api.deepseek.com':
                result['error']='当前接口不是 DeepSeek 官方网页抓取接口';return result
            client=await engine._get_client()
            response=await client.post('https://api.deepseek.com/v1/tools/fetch',json={'url':url,'max_content_tokens':5000},
                                       timeout=httpx.Timeout(settings.web_search_timeout))
            response.raise_for_status();data=response.json();result['requests']=1
            text=str(data.get('content') or '')
            if text:result.update(ok=True,text=text[:20000],title=str(data.get('title') or '')[:250])
            else:result['error']='网页未返回可读正文'
        except httpx.TimeoutException:result['error']='网页抓取超时'
        except httpx.HTTPStatusError as exc:result['error']='网页抓取接口 HTTP '+str(exc.response.status_code)
        except httpx.HTTPError:result['error']='网页抓取接口暂时无法连接'
        except (ValueError,TypeError):result['error']='网页抓取结果暂不可用'
        finally:
            result['seconds']=round(time.monotonic()-started,3);result['checked_at']=time.time()
            self.last={k:v for k,v in result.items() if k!='text'}
        return result
