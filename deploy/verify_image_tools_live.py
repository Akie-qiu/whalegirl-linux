"""A few real chat-model decisions in an isolated store; no WeChat sends or image API calls."""
import asyncio
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT=Path('/opt/whalegirl-linux/current').resolve()
sys.path.insert(0,str(ROOT))
from whalegirl_linux.settings import Config
from whalegirl_linux.store import Store
from whalegirl_linux.moments import MomentsBook
from whalegirl_linux.images import ImageService
from whalegirl_linux.image_references import ReferenceLibrary,VARIANTS
from whalegirl_linux.token_usage import TokenLedger
from whalegirl_linux.stickers import parse_json
from whalegirl_linux.upstream.ai_engine import AIEngine
from whalegirl_linux.upstream.style_manager import StyleManager


async def main():
    live=Config(Path('/var/lib/whalegirl-linux/core'))
    directory=Path(tempfile.mkdtemp(prefix='image-tools-model-',dir='/opt/whalegirl-linux-assets'));directory.chmod(0o700)
    config=Config(directory)
    config.save(live.settings.model_copy(update={'enabled':True,'allowed_chat_ids':['check','check@chatroom'],
        'images_allowed_chat_ids':[],'image_per_day':100}),live.api_key())
    config.save_image_key(live.image_api_key())
    references=ReferenceLibrary(live);target=ReferenceLibrary(config);target.root.mkdir(parents=True,exist_ok=True)
    items=[v for v in references.manifest()['items'] if v['variant'] in VARIANTS]
    for item in items:shutil.copy2(references.path(item['id']),target.path(item['id']))
    (target.root/'index.json').write_text(json.dumps({'items':items},ensure_ascii=False))
    store=Store(directory/'isolated.sqlite3');book=MomentsBook(store)
    styles=StyleManager(styles_dir=str(ROOT/'styles'),custom_dir=str(ROOT/'styles'/'custom'),locked_name='tsundere_whale_girl')
    styles.load_all();styles.set_active('tsundere_whale_girl')
    native=directory/'native';native.mkdir()
    runtime=SimpleNamespace(store=store,config=config,styles=styles,engine_factory=AIEngine,token_ledger=TokenLedger(store),
        gateway=SimpleNamespace(ui_jobs_directory=native),moments=SimpleNamespace(book=book,lock=asyncio.Lock()))
    images=ImageService(runtime)
    expected_character_refs=len(images.references.selected(config.settings.image_character_variant))
    autonomy='--autonomy' in sys.argv
    asset=images.save_asset(target.path(items[0]['id']).read_bytes(),'generated' if autonomy else 'uploaded',scope='global',character_id='whalegirl',character_variant=items[0]['variant'])
    images.edit_asset(asset['id'],{'name':'大肥鱼角色展示图','description':'已确认的大肥鱼本人角色图','scenes':'展示自身外观'})
    engine=images.new_engine();results=[]
    cases=[('appearance','你知道你长什么样吗',False,[]),
        ('explicit_self','帮我画一张你自己，穿冬装，背景下雪',True,[]),
        ('context_self','那你画一张',True,[{'role':'user','content':'你知道你长什么样吗'},{'role':'assistant','content':'蓝头发蓝眼睛，还有鲸鱼尾巴呀'}]),
        ('other_member','@其他群友 你画一张猫',False,[]),
        ('library_self','发一张你自己的图片',True,[])]
    if autonomy:
        images.edit_asset(asset['id'],{'name':'昨晚画好的冬装角色图','description':'以前生成好的大肥鱼本人，蓝发蓝眼，穿冬装站在雪地里','scenes':'重发昨晚的旧图'})
        cases=[('colloquial_edit','给她整身女仆装呗',True,[]),
               ('reuse_generated','昨晚那张 再给我瞅瞅',True,[{'role':'assistant','content':'昨晚的冬装图我存着呢'}]),
               ('context_self','就按咱刚才说的整一张',True,[{'role':'user','content':'想看你穿冬装站在雪地里的新画面'},{'role':'assistant','content':'蓝头发蓝眼睛，围条毛巾站雪地里那种吗'}])]
    try:
        for index,(label,text,expected,history) in enumerate(cases):
            chat='check@chatroom' if label=='other_member' else 'check'
            first={'key':'model-check/'+str(index),'account':config.settings.account_id,'chat':chat,'sender_id':'check-'+str(index),
                'sender':'检查请求者','content':text,'kind':1,'is_self':0,'trigger':'natural' if label=='other_member' else 'direct','timestamp':time.time()}
            model_text=text
            if label=='colloquial_edit':
                source={'key':config.settings.account_id+'/check/100','account':config.settings.account_id,'chat':chat,
                    'sender_id':first['sender_id'],'sender':first['sender'],'kind':3,'is_self':0,'local_id':100,'timestamp':time.time()-5,'content':'[图片]'}
                store.observe([source]);first['payload_json']=json.dumps({'reply':{'type':3,'serverId':'100'}})
                model_text='[引用图片，原图key='+source['key']+'，已识别画面：金发双马尾少女躺在床上，穿粉蓝连衣裙]\n当前发言：'+text
            if label=='reuse_generated':
                config.save(config.settings.model_copy(update={'image_per_day':0}))
            elif label=='context_self':
                config.save(config.settings.model_copy(update={'image_per_day':100}))
            instructions,context=images.tools.context(first,[first])
            started=time.monotonic()
            answer=await engine.chat(history+[{'role':'user','content':model_text}],
                system_prompt=(config.settings.system_prompt_override or styles.get_active().system_prompt)+instructions+
                '\n只输出JSON：{"should_reply":true,"actions":[{"type":"text","text":"按人设自然接话"}],"image_request":null}。需要图片工具时按上方格式填写image_request。',
                max_tokens=900,response_format={'type':'json_object'})
            decision=parse_json(answer)
            if not isinstance(decision,dict):raise RuntimeError('Invalid model decision: '+label)
            request=decision.get('image_request')
            result=images.tools.execute(request,first,[first],context)
            accepted=bool(result.get('ok') and result.get('job_id'))
            ref=json.loads(images.get(result['job_id'])['reference_json']) if accepted else {}
            passed=accepted==expected and (not expected or label=='library_self' or ref.get('character') and len(ref.get('inputs',[]))==expected_character_refs)
            if autonomy:
                passed=bool(accepted and (
                    label=='colloquial_edit' and not ref.get('character') and len(ref.get('sources',[]))==1
                    or label=='reuse_generated' and request.get('tool')=='send_library_image'
                    or label=='context_self' and ref.get('character') and len(ref.get('inputs',[]))==expected_character_refs))
            summary={'case':label,'passed':bool(passed),'tool':request.get('tool') if isinstance(request,dict) else None,
                'code':result['code'],'character_references':len(ref.get('inputs',[])),'seconds':round(time.monotonic()-started,2)}
            results.append(summary);print(json.dumps(summary),flush=True)
        report={'at':time.time(),'cases':results,'production_image_requests':0,'wechat_messages_sent':0}
        filename='image-tools-autonomy-model-acceptance.json' if autonomy else 'image-tools-model-acceptance.json'
        Path('/opt/whalegirl-linux-assets',filename).write_text(json.dumps(report,indent=2))
        if not all(r['passed'] for r in results):raise RuntimeError('Model acceptance needs repair')
    finally:
        await engine.close();store.close()


if __name__=='__main__':asyncio.run(main())
