"""One isolated reflection of cached public video material; no platform writes."""
import argparse
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import resource
import sqlite3
import sys
import time

from evaluate_focus_chat import build, memory


CORE=Path('/var/lib/whalegirl-linux/core')


def protected():
    return {name:hashlib.sha256((CORE/name).read_bytes()).hexdigest() for name in
            ('settings.json','persona-cards.json','model-profiles.json','model-api-keys.enc','bilibili-session.enc')
            if (CORE/name).exists()}


async def run(args):
    args.code=args.code.resolve();args.out=args.out.resolve()
    if args.out.parent.parent!=Path('/tmp') or not args.out.parent.name.startswith('whalegirl-speech-eval-'):
        raise RuntimeError('invalid_sandbox')
    args.out.mkdir(mode=0o700,parents=True,exist_ok=True)
    if memory()['available_mib']<144:raise RuntimeError('host_memory_guard')
    before=protected();source=None
    with sqlite3.connect('file:'+str(CORE/'state.sqlite3')+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('PRAGMA cache_size=-1024')
        for row in db.execute("SELECT url,title,body FROM growth_sources WHERE kind='bilibili' ORDER BY created DESC LIMIT 12"):
            try:material=json.loads(row['body'])
            except (TypeError,ValueError):continue
            if isinstance(material,dict) and len(material.get('subtitles') or [])>=2:
                source={'kind':'bilibili','url':row['url'],'title':row['title'],'content':row['body']};break
    if not source:raise RuntimeError('no_cached_subtitle_material')
    sys.path.insert(0,str(args.code));runtime,gateway,calls,config=build(args,args.out,real=True)
    started=time.monotonic();report={'config':config,'synthetic_input':False};engine=None
    try:
        runtime.config.save(runtime.config.settings.model_copy(update={'bilibili_enabled':True,
            'growth_enabled':True,'growth_auto_apply':True,'growth_internet_model_profile':'current'}))
        growth=runtime.growth;role=runtime.memory_hub.role();row=growth.save_source(source,role)
        if not row:raise RuntimeError('cached_source_not_accepted')
        engine=growth.worker_engine('bilibili')
        engine.usage_callback=growth.record_usage
        config.update(reasoning='low',worker_profile='current',temperature=.55)
        result=await growth.reflect_sources(engine,runtime.config.generation,role,[row],'从实际视频台词学习口语表达')
        learned=growth.memories();evidence=[]
        for item in learned:
            record=runtime.store.db.execute('SELECT receipt FROM memory_evidence WHERE memory_id=?',(item['id'],)).fetchone()
            receipt=json.loads(record[0]) if record else {}
            event=receipt.get('reaction_event') or {}
            evidence.append({'category':item['category'],'summary':item['content'],'scene':item.get('scene'),
                'conditions':item.get('conditions'),'examples':item.get('examples'),
                'evidence_kind':event.get('reaction_kind'),'speaker_verification':event.get('speaker_verification')})
        journal=growth.journal(result['journal_id']) if result.get('journal_id') else {}
        report={'synthetic_input':False,'input':'previously cached public video material, read-only',
            'config':config,'calls':calls,'elapsed_seconds':round(time.monotonic()-started,4),
            'result':result,'speech_summary':journal.get('body',{}).get('speech_summary',''),
            'missing':journal.get('body',{}).get('missing',''),'learned':evidence,
            'coverage':growth.source(row['id'])['coverage'].get('reflection_input'),
            'usage':runtime.token_ledger.snapshot(runtime.account_id),'native_sends':len(gateway.sent)}
        report['success']=True
    except Exception as error:
        report.update(success=False,error_type=type(error).__name__,
            error_code='output_length' if 'length' in str(error) else 'reflection_incomplete')
    finally:
        report.update(calls=calls,elapsed_seconds=round(time.monotonic()-started,4),
            usage=runtime.token_ledger.snapshot(runtime.account_id),native_sends=len(gateway.sent))
        if engine:await engine.close()
        await runtime.close()
        report.update(peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2),
            production_configuration_unchanged=before==protected(),host_after=memory())
        path=args.out/'result.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8');path.chmod(0o600)
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--code',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();args.context=100;args.temperature=None;args.natural_mode=False
    os.environ['LOGURU_LEVEL']='ERROR'
    logging.disable(logging.CRITICAL)
    from loguru import logger
    logger.remove()
    report=asyncio.run(run(args))
    print(json.dumps({'calls':len(report['calls']),'learned':len(report['learned']),
        'subtitle_expressions':sum(row['evidence_kind']=='subtitle' for row in report['learned']),
        'configuration_unchanged':report['production_configuration_unchanged']},ensure_ascii=True))


if __name__=='__main__':main()
