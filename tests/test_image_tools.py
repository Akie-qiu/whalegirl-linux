"""Regression checks for the user-reported image intent and identity failures."""
import asyncio
import base64
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import httpx

from PIL import Image
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store
from test_chat_habits import Gateway, Engine

ROOT = Path(__file__).resolve().parents[1]


class ImageToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.path = Path(tempfile.mkdtemp(prefix='image-tools-', dir=ROOT/'test-runs'))
        self.config = Config(self.path)
        self.config.save(Settings(enabled=True, account_id='account', allowed_chat_ids=['private','group@chatroom'],
            image_per_day=10, debounce_single_seconds=0, debounce_burst_seconds=0, debounce_max_seconds=0,
            stickers_enabled=False, chat_cooldown=5, min_send_interval=3), 'test-key')
        self.config.save_image_key('test-image-key')
        self.store = Store(self.path/'state.sqlite3')
        self.gateway = Gateway()
        self.gateway.ui_jobs_directory = self.path/'native';self.gateway.ui_jobs_directory.mkdir()
        self.runtime = Runtime(self.config, self.store, self.gateway, ROOT/'styles', engine_factory=Engine)
        self.images = self.runtime.images
        raw = io.BytesIO();Image.new('RGB',(20,20),'blue').save(raw,format='PNG');self.raw=raw.getvalue()
        self.images.references.add(self.raw,'girl','少女版','角色外观')

    async def asyncTearDown(self):
        await self.runtime.close()
        self.store.close()

    def message(self, text, mid=1, chat='private', **extra):
        return dict(key=f'account/{chat}/{mid}', account='account', chat=chat, sender='alice', sender_id='alice',
                    kind=1, is_self=0, local_id=mid, content=text, timestamp=time.time(), trigger='direct', **extra)

    def request(self, **extra):
        return dict(tool='generate_image', subject_type='self', subject_resolved=True,
                    scene_prompt='大肥鱼少女版穿冬装在雪地微笑，蓝发蓝眼鲸鱼特征', authorization='帮我画一张你自己',
                    directed_to_bot=True, target_basis='private', operation='create', **extra)

    def execute(self, first, request):
        _, context = self.images.tools.context(first,[first])
        return self.images.tools.execute(request,first,[first],context)

    def count(self):
        return self.store.db.execute('SELECT count(*) FROM picture_jobs').fetchone()[0]

    async def test_normal_appearance_chat_uses_one_model_turn_and_never_submits(self):
        first=self.message('你知道你长什么样吗')
        self.store.insert(first);self.store.observe([first])
        engine=await self.runtime.model();engine.answers=[{'should_reply':True,'actions':[{'type':'text','text':'蓝头发蓝眼睛呀'}]}]
        await self.runtime.process_next()
        self.assertEqual(engine.calls,1)
        self.assertEqual(self.count(),0)
        self.assertEqual(self.gateway.sent,[('text','蓝头发蓝眼睛呀')])
        prompt,ctx=self.images.tools.context(first,[first])
        self.assertIn('少女版',prompt);self.assertIn('就是你本人',prompt)
        self.assertEqual(ctx['candidates'],[])

    async def test_model_ack_and_self_reference_are_kept_together(self):
        first=self.message('帮我画一张你自己')
        self.store.insert(first);self.store.observe([first])
        engine=await self.runtime.model();engine.answers=[{'should_reply':True,'image_request':self.request(),
            'actions':[{'type':'text','text':'那我穿件冬装吧'}]}]
        await self.runtime.process_next()
        self.assertEqual(engine.calls,1)
        self.assertEqual(self.count(),1)
        job=self.images.get(self.images.tools.recent(first)[0]['id'])
        plan=json.loads(job['reference_json'])
        self.assertTrue(plan['character']);self.assertEqual(plan['variant'],'girl')
        self.assertEqual(plan['inputs'][0]['source'],'character')
        self.assertTrue(plan['appearance']);self.assertIn('冬装',job['prompt'])
        self.assertEqual(self.gateway.sent,[('text','那我穿件冬装吧')])

    def test_model_decision_does_not_need_phrase_or_authorization_matching(self):
        request={'tool':'generate_image','subject_type':'other','scene_prompt':'海边的一只蓝鲸，柔和水彩插画'}
        first=self.message('就按咱们刚才聊的整一个吧',chat='group@chatroom')
        self.assertTrue(self.execute(first,request)['ok'])
        self.assertEqual(self.count(),1)

    def test_clarification_and_no_match_cannot_create_paid_jobs(self):
        first=self.message('那你画一张')
        request=self.request();request.update(scene_prompt='')
        self.assertEqual(self.execute(first,request)['code'],'scene_unclear')
        request.update(tool='send_library_image',subject_resolved=True,asset_id='a'*64,authorization='从图库挑张图片发我')
        self.assertEqual(self.execute(self.message('从图库挑张图片发我'),request)['code'],'no_match')
        self.assertEqual(self.count(),0)

    def test_quoted_pronoun_edit_binds_original_not_bot_character(self):
        source=self.message('[图片]',90);source['kind']=3
        self.store.observe([source])
        text='把它的衣服换成女仆装'
        first=self.message(text,91,payload_json=json.dumps({'reply':{'type':3,'serverId':'90'}}))
        request=self.request();request.update(subject_type='other',authorization=text,mode='edit',
            scene_prompt='保留金发双马尾少女的脸、姿势和构图，只把衣服换成女仆装',reference_keys=[source['key']])
        result=self.execute(first,request)
        self.assertTrue(result['ok'],result)
        plan=json.loads(self.images.get(result['job_id'])['reference_json'])
        self.assertEqual([v['key'] for v in plan['sources']],[source['key']])
        self.assertFalse(plan['character']);self.assertEqual(plan['inputs'],[])
        self.assertEqual(plan['mode'],'edit');self.assertEqual(self.count(),1)

    def test_ai_selected_reference_supports_colloquial_edit_without_keywords(self):
        request=self.request();request.update(subject_type='other',authorization='把它的衣服换成女仆装',scene_prompt='保留原角色，只把衣服换成女仆装',mode='edit')
        self.assertEqual(self.execute(self.message(request['authorization']),request)['code'],'reference_not_available')
        source=self.message('[图片]',90);source['kind']=3;self.store.observe([source])
        request={'tool':'generate_image','subject_type':'other','scene_prompt':'原图角色身穿可爱女仆裙，其他保留','reference_keys':[source['key']],'mode':'edit'}
        first=self.message('给她整身女仆装呗',91)
        result=self.execute(first,request);self.assertTrue(result['ok'],result)
        self.assertIn(source['key'],self.images.get(result['job_id'])['reference_json'])
        request['reference_keys']=['account/other/90']
        self.assertFalse(self.execute(self.message('再改改',92),request)['ok'])
        self.assertEqual(self.count(),1)

    def test_merge_before_submission_and_no_duplicate_after_submission(self):
        first=self.message('帮我画一张你自己')
        result=self.execute(first,self.request());self.assertTrue(result['ok'],result)
        amendment=self.request();amendment.update(operation='amend',task_id=result['job_id'],authorization='穿冬装，背景下雪')
        second=self.message('穿冬装，背景下雪',2)
        self.assertEqual(self.execute(second,amendment)['code'],'amended')
        self.assertEqual(self.count(),1)
        self.assertEqual(self.execute(first,self.request())['code'],'duplicate')
        self.images.change(result['job_id'],state='generating',api_started=time.time())
        amendment.update(authorization='背景改成海边')
        third=self.message('背景改成海边',3)
        self.assertEqual(self.execute(third,amendment)['code'],'already_submitted')
        self.assertEqual(self.count(),1)
        self.assertTrue(self.images.tools.recent(third)[0]['submitted'])

    def test_model_can_choose_no_tool_for_other_members_conversation(self):
        first=self.message('@小明 你画一张猫',chat='group@chatroom')
        self.assertEqual(self.execute(first,None)['code'],'no_tool')
        self.assertEqual(self.count(),0)

    def test_library_permissions_and_character_identity(self):
        personal=self.images.save_asset(self.raw,'generated',scope='chat',scope_chat='private')
        public=self.images.save_asset(self.raw,'generated',scope='global')
        self.assertNotEqual(personal['id'],public['id'])
        self.assertNotIn(personal['id'],{a['id'] for a in self.images.candidates(chat='group@chatroom')})
        with self.assertRaisesRegex(ValueError,'使用范围'):
            self.images.stage(personal['id'],chat='group@chatroom')
        first=self.message('发一张你自己的图片')
        _,ctx=self.images.tools.context(first,[first]);self.assertIn(public['id'],{a['id'] for a in ctx['candidates']})
        values={k:public[k] for k in ('name','description','scenes','enabled')}
        self.images.edit_asset(public['id'],{**values,'character_id':'whalegirl','character_variant':'girl'})
        _,ctx=self.images.tools.context(first,[first]);self.assertIn(public['id'],{a['id'] for a in ctx['candidates']})
        self.images.edit_asset(public['id'],{**values,'scope':'admin'})
        self.assertEqual(self.images.tools.execute(dict(tool='send_library_image',subject_type='self',subject_resolved=True,
            asset_id=public['id'],authorization=first['content']),first,[first],ctx)['code'],'asset_scope_denied')

    def test_cached_catalog_keeps_current_ranking_and_rejects_unrelated_selection(self):
        blue = self.images.save_asset(self.raw, scope='global')
        raw = io.BytesIO(); Image.new('RGB', (20,20), 'red').save(raw, format='PNG')
        red = self.images.save_asset(raw.getvalue(), scope='global')
        self.images.edit_asset(blue['id'], dict(name='雪景',description='安静的白色雪原',scenes='发泳装照片时',enabled=True))
        self.images.edit_asset(red['id'], dict(name='泳装少女',description='少女穿着蓝色泳装',scenes='夏天',enabled=True))
        first = self.message('发一张泳装照片')
        _, ctx = self.images.tools.context(first, [first])
        prompt = self.images.tools.dynamic_prompt(ctx, {blue['id'], red['id']})
        payload = json.loads(prompt.split('：', 1)[1])
        self.assertEqual(payload['candidates'], [])
        self.assertEqual(payload['search_matches'][0]['id'], red['id'])
        self.assertIn('泳装', payload['search_matches'][0]['matching_terms'])
        self.assertEqual(self.execute(first, dict(tool='send_library_image', asset_id=blue['id']))['code'], 'no_match')
        self.assertEqual(self.count(), 0)
        self.assertTrue(self.execute(first, dict(tool='send_library_image', asset_id=red['id']))['ok'])
        self.assertEqual(self.images.get(self.images.tools.recent(first)[0]['id'])['kind'], 'send')

    async def test_rejected_tool_replaces_success_ack_with_model_explanation(self):
        first=self.message('那你画一张')
        self.store.insert(first);self.store.observe([first])
        request=self.request();request.update(scene_prompt='')
        engine=await self.runtime.model();engine.answers=[{'should_reply':True,'image_request':request,
            'actions':[{'type':'text','text':'我开始画啦'}]}, {'should_reply':True,'actions':[{'type':'text','text':'你想画啥呀'}]}]
        await self.runtime.process_next()
        self.assertEqual(engine.calls,2)
        self.assertEqual(self.gateway.sent,[('text','你想画啥呀')]);self.assertEqual(self.count(),0)

    async def test_amend_during_reference_loading_is_the_single_provider_request(self):
        first=self.message('帮我画一张你自己')
        result=self.execute(first,self.request())
        started=asyncio.Event();finish=asyncio.Event();calls=[]
        original_materialize=self.images.references.materialize
        # generate() uses an account-bound library instance.
        from whalegirl_linux.image_references import ReferenceLibrary
        async def materialize(library,*args):
            started.set();await finish.wait()
            return await original_materialize(*args)
        async def handler(request):
            body=await request.aread();calls.append(body)
            self.assertIn('背景改成雪地和冬装'.encode(),body)
            return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(self.raw).decode()}]})
        original_client=httpx.AsyncClient
        with patch.object(ReferenceLibrary,'materialize',materialize), patch('whalegirl_linux.images.httpx.AsyncClient',
                side_effect=lambda **kw:original_client(transport=httpx.MockTransport(handler),**kw)):
            work=asyncio.create_task(self.images.generate_once());await started.wait()
            request=self.request();request.update(operation='amend',task_id=result['job_id'],authorization='背景改成雪地和冬装',scene_prompt='大肥鱼少女版，背景改成雪地和冬装，蓝发蓝眼')
            try:
                self.assertEqual(self.execute(self.message('背景改成雪地和冬装',2),request)['code'],'amended')
            finally:
                finish.set();await work
        self.assertEqual(len(calls),1);self.assertEqual(self.count(),1)
        self.assertEqual(self.images.get(result['job_id'])['state'],'ready')

    def test_restart_does_not_reset_admin_asset_restrictions(self):
        from whalegirl_linux.image_tools import ImageTools
        asset=self.images.save_asset(self.raw,scope='global')
        self.images.edit_asset(asset['id'],{'scope':'admin'})
        ImageTools(self.images)
        self.assertEqual(self.images.asset(asset['id'])['scope'],'admin')

    async def test_old_generated_picture_can_repeat_without_generation_or_send_quota(self):
        from whalegirl_linux.gateway import Receipt
        self.config.save(self.config.settings.model_copy(update={'image_per_day':0,'safety_per_minute':1,
            'safety_per_hour':1,'safety_per_day':1,'safety_chat_per_hour':1}))
        asset=self.images.save_asset(self.raw,'generated',scope='chat',scope_chat='private')
        self.images.edit_asset(asset['id'],{'name':'以前生成的冬装图','description':'蓝发少女穿冬装，雪景'})
        original=self.message('之前生成的图片',80)
        sent=self.store.action_start(original['key'],original,0,{'type':'text','display':'达到文字限额'})
        self.store.action_finish(sent,'sent','test-text')
        self.runtime.processing='';self.runtime.last_send=0
        async def send_image(chat,identity):
            self.assertEqual(identity,asset['id']);return Receipt(True,'test-image')
        self.gateway.send_image=send_image;self.gateway.status_for_send=self.gateway.status
        ids=[]
        for index in (81,82):
            first=self.message('就刚才那张，再给我看看',index)
            _,ctx=self.images.tools.context(first,[first])
            self.assertIn(asset['id'],{a['id'] for a in ctx['candidates']})
            result=self.images.tools.execute({'tool':'send_library_image','asset_id':asset['id']},first,[first],ctx)
            self.assertTrue(result['ok'],result);ids.append(result['job_id'])
            # Time spacing is independent of quotas; avoid waiting in this check.
            with patch.object(self.store,'latest_sent',return_value=0):
                self.runtime.last_send=0
                await self.images.process_once()
            self.assertEqual(self.images.get(result['job_id'])['state'],'sent')
        self.assertEqual(self.images.generation_count(),0)
        self.assertEqual(self.store.db.execute("select count(*) from picture_jobs where kind='generate'").fetchone()[0],0)
        self.assertNotEqual(ids[0],ids[1])
        counts,_,_=self.runtime.safety.activity('account')
        self.assertEqual(counts['day'],1);self.assertEqual(counts['image_day'],2)

    def test_daily_generation_cap_counts_submissions_and_pending_not_preflight_failures(self):
        self.config.save(self.config.settings.model_copy(update={'image_per_day':1}))
        first=self.message('来一个',1);result=self.execute(first,self.request())
        self.assertTrue(result['ok'])
        self.assertEqual(self.execute(self.message('再来一个',2),self.request())['code'],'daily_limit')
        self.images.change(result['job_id'],state='failed',error='参考图未取得，未调用接口')
        second=self.execute(self.message('好再试试',3),self.request());self.assertTrue(second['ok'],second)
        self.images.change(second['job_id'],state='cancelled',api_started=time.time())
        self.assertEqual(self.execute(self.message('换一张吧',4),self.request())['code'],'daily_limit')

    async def test_exhausted_text_quota_does_not_stop_ai_library_tool_decision(self):
        self.config.save(self.config.settings.model_copy(update={'image_per_day':0,'safety_per_day':1}))
        asset=self.images.save_asset(self.raw,'generated',scope='chat',scope_chat='private')
        old=self.message('old',80)
        action=self.store.action_start(old['key'],old,0,{'type':'text','display':'previous text'})
        self.store.action_finish(action,'sent','text-1')
        first=self.message('给我再瞅瞅',81);self.store.insert(first);self.store.observe([first])
        engine=await self.runtime.model();engine.answers=[{'should_reply':True,'actions':[],
            'image_request':{'tool':'send_library_image','asset_id':asset['id']}}]
        with patch.object(self.store,'latest_sent',return_value=0):
            await self.runtime.process_next()
        self.assertEqual(engine.calls,1)
        self.assertEqual(self.images.tools.recent(first)[0]['asset_id'],asset['id'])
        self.assertEqual(self.images.tools.recent(first)[0]['tool'],'send_library_image')


if __name__=='__main__':unittest.main()
