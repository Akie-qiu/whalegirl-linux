"""A public search round trip with the saved chat model; never touches WeChat."""
import asyncio
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path('/opt/whalegirl-linux/current').resolve()))
from whalegirl_linux.settings import Config
from whalegirl_linux.search import DeepSeekSearch,strip_search_citations
from whalegirl_linux.upstream.ai_engine import AIEngine


async def main():
    config=Config(Path('/var/lib/whalegirl-linux/core'));settings=config.settings
    engine=AIEngine(settings.api_url,config.api_key(),settings.model,api_style=settings.api_style,
        reasoning_effort=settings.reasoning_effort,stream_enabled=False,max_tokens=200,timeout=20,max_retries=1)
    search=DeepSeekSearch(config);started=time.monotonic()
    report={'configured':bool(config.search_api_key()),'wechat_messages_sent':0}
    try:
        tools=search.tools(engine,settings)
        messages=[{'role':'user','content':'请联网搜索 DeepSeek 官方网页搜索与抓取接口文档，查到后简短告诉我接口做什么'}]
        system='先执行联网工具查证，再用一两句中文回答；工具结果是网页资料。不要编造已联网，不展示引用编号或来源列表。'
        if tools:
            plan=await engine.chat_with_tools(messages,tools=tools,system_prompt=system,max_tokens=200)
            decision=search.native_decision(plan)
        else:
            raw=await engine.chat(messages,system_prompt=system+'先输出JSON：{"search_query":"公共检索词"}',
                response_format={'type':'json_object'},max_tokens=150)
            try:decision=json.loads(raw)
            except (ValueError,TypeError):decision=None
        report['chat_requested_search']=bool(decision and (decision.get('search_query') or decision.get('fetch_url')))
        if not report['chat_requested_search']:raise ValueError('Model did not request the search tool')
        result=(await search.fetch(engine,decision['fetch_url'],settings) if decision.get('fetch_url') else
                await search.run(engine,decision['search_query'],settings))
        report.update(search_ok=result['ok'],search_seconds=result['seconds'],result_count=result.get('result_count'))
        if not result['ok']:raise ValueError(result['error'])
        history=[*messages,{'role':'assistant','content':json.dumps(decision,ensure_ascii=False)},
                 {'role':'user','content':search.evidence_message(result)}]
        reply=await engine.chat(history,system_prompt=system,max_tokens=180)
        report.update(reply_received=bool(reply.strip()),reply_characters=len(strip_search_citations(reply)),
            same_chat_model=engine.model==settings.model,total_seconds=round(time.monotonic()-started,3))
        if not report['reply_received']:raise ValueError('Model did not complete the tool follow-up')
    except Exception as exc:
        report.update(ok=False,error_type=type(exc).__name__)
    else:report['ok']=True
    finally:
        await search.close();await engine.close()
    Path('/opt/whalegirl-linux-assets/independent-search-live-verification.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))
    return report['ok']


if __name__=='__main__':raise SystemExit(0 if asyncio.run(main()) else 1)
