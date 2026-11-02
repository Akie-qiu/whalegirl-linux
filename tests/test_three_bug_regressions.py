"""Synthetic HTTP, library and receipt races; no network or WeChat sends."""
import asyncio
import base64
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import test_image_tools as fixture
from whalegirl_linux.gateway import Receipt
from whalegirl_linux.delivery import reconcile_outbound
from whalegirl_linux.upstream.ai_engine import AIEngine
from whalegirl_linux.model_reply import guarded_chat, ToolReplyFormatError


class LengthBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def run_model(self, responses, *, tools=False, style='openai', retries=2):
        requests, usage = [], []
        engine = AIEngine('https://example.test/v1', 'test-key', model='deepseek-v4.1-flash',
                          reasoning_effort='medium', api_style=style, max_retries=retries)
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=responses[len(requests)-1])
        engine._clients[id(asyncio.get_running_loop())] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        engine.usage_callback = usage.append
        try:
            options = dict(system_prompt='固定人设', max_tokens=220, response_format={'type':'json_object'})
            if tools:
                result = await engine.chat_with_tools([], [{'type':'function','function':{
                    'name':'wait_for_next_message','parameters':{'type':'object'}}}], **options)
            else:
                result = await engine.chat([], **options)
            return result, requests, usage, engine.last_error, engine.last_response_meta
        finally:
            await engine.close()

    @staticmethod
    def response(finish='stop', content='{"actions":[]}', calls=None):
        return {'choices':[{'finish_reason':finish,'message':{
            'role':'assistant','content':content,'reasoning_content':'synthetic reasoning',
            'tool_calls':calls or []}}], 'usage':{'prompt_tokens':40,'completion_tokens':220,
                'completion_tokens_details':{'reasoning_tokens':210}}}

    def test_reasoning_reserve_does_not_change_persona_or_effort_or_streaming(self):
        engine = AIEngine('https://example.test/v1','test',model='deepseek-v4.1-flash',reasoning_effort='medium')
        plain = engine._build_payload([], '人设', None, max_tokens=870)
        self.assertEqual(plain['max_tokens'],2918)
        self.assertEqual(plain['reasoning_effort'],'medium')
        self.assertEqual(engine._build_payload([], '人设', None, max_tokens=870,stream=True)['max_tokens'],870)
        engine.model='normal-model';engine.reasoning_effort='default'
        self.assertEqual(engine._build_payload([], '人设', None,max_tokens=870)['max_tokens'],870)

    async def test_truncated_native_arguments_never_escape_before_complete_retry(self):
        call={'id':'complete','type':'function','function':{
            'name':'wait_for_next_message','arguments':'{"reason":"还有下一句"}'}}
        partial={**call,'function':{**call['function'],'arguments':'{"reason":'}}
        result, requests, usage, error, meta = await self.run_model([
            self.response('length','', [partial]),self.response('tool_calls','',[call])], tools=True)
        self.assertEqual(result['tool_calls'],[call]);self.assertEqual(error,'')
        self.assertEqual(len(requests),2)
        self.assertGreater(requests[1]['max_tokens'],requests[0]['max_tokens'])
        self.assertEqual({k:v for k,v in requests[0].items() if k!='max_tokens'},
                         {k:v for k,v in requests[1].items() if k!='max_tokens'})
        self.assertEqual([v['status'] for v in usage],['error','success'])
        self.assertEqual(meta['recovered_from'],'length')

    async def test_two_truncations_end_without_partial_decision(self):
        result, requests, usage, error, meta = await self.run_model([self.response('length'),self.response('length')])
        self.assertEqual(result,'');self.assertIn('length',error);self.assertEqual(len(requests),2)
        self.assertEqual(len(usage),2)

    async def test_disabled_retry_stays_one_request(self):
        result, requests, _, error, _ = await self.run_model([self.response('length')],retries=0)
        self.assertFalse(result);self.assertEqual(len(requests),1);self.assertIn('length',error)

    async def test_content_filter_never_treated_as_token_recovery(self):
        result, requests, _, error, _ = await self.run_model([self.response('content_filter')],tools=True)
        self.assertFalse(result['content']);self.assertEqual(len(requests),1);self.assertIn('content_filter',error)

    async def test_invalid_json_tool_arguments_are_not_callable(self):
        call={'id':'bad','function':{'name':'wait_for_next_message','arguments':'{"reason":'}}
        result, requests, _, error, _ = await self.run_model([self.response('tool_calls','',[call])],tools=True)
        self.assertFalse(result['tool_calls']);self.assertEqual(len(requests),1);self.assertIn('完整JSON',error)

    async def test_responses_and_anthropic_limits_have_bounded_recovery(self):
        for style, responses in [
            ('responses',[{'status':'incomplete','incomplete_details':{'reason':'max_output_tokens'}},
                          {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'{"actions":[]}'}]}]}]),
            ('anthropic',[{'stop_reason':'max_tokens','content':[{'type':'text','text':'{"actions":'}]},
                          {'stop_reason':'end_turn','content':[{'type':'text','text':'{"actions":[]}'}]}])]:
            with self.subTest(style=style):
                result, requests, _, error, _ = await self.run_model(responses,style=style)
                self.assertEqual(result,'{"actions":[]}');self.assertEqual(error,'');self.assertEqual(len(requests),2)

    async def test_format_guard_does_not_multiply_exhausted_length_retries(self):
        calls=[]
        async def chat(*args,**kwargs):calls.append(kwargs);return ''
        engine=SimpleNamespace(chat=chat,last_error='模型回复未完成：length')
        with self.assertRaises(ToolReplyFormatError):
            await guarded_chat(engine,[],diagnostics={},stage='image_result')
        self.assertEqual(len(calls),1)


class ImageBugTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ImageToolTests.asyncSetUp
    asyncTearDown = fixture.ImageToolTests.asyncTearDown
    message = fixture.ImageToolTests.message

    def asset(self,source='tieba',name='小猫',description='猫坐在窗边',stamp=None,scope='global'):
        self.config.save(self.config.settings.model_copy(update={'tieba_enabled':True,'tieba_images_enabled':True}))
        asset=self.images.save_asset(self.raw,source,scope=scope)
        self.images.edit_asset(asset['id'],dict(name=name,description=description,scenes='日常',enabled=True))
        if stamp is not None:
            self.store.db.execute('UPDATE picture_assets SET created=? WHERE id=?',(stamp,asset['id']))
            self.store.db.commit()
        return self.images.asset(asset['id'])

    async def test_tieba_source_only_request_reaches_dispatch_without_generation(self):
        asset=self.asset();self.asset('generated',name='生成猫图')
        self.config.save(self.config.settings.model_copy(update={'min_send_interval':0,'chat_cooldown':0}))
        first=self.message('发一张贴吧素材图片')
        _,ctx=self.images.tools.context(first,[first])
        self.assertEqual(ctx['requested_source'],'tieba')
        self.assertEqual([a['id'] for a in ctx['candidates']],[asset['id']])
        req=self.images.tools.direct_library_fallback(first,ctx)
        self.store.insert(first);self.store.observe([first])
        engine=await self.runtime.model()
        engine.answers=[{'should_reply':True,'actions':[], 'image_request':req}]
        await self.runtime.process_next()
        self.runtime.processing=''  # The normal worker releases this before dispatch.
        async def send_image(chat,asset_id):
            self.gateway.sent.append(('image',asset_id));return Receipt(True,'native-1')
        self.gateway.status_for_send=self.gateway.status;self.gateway.send_image=send_image
        await self.images.process_once()
        self.assertIn(('image',asset['id']),self.gateway.sent)
        self.assertEqual(self.images.generation_count(),0)

    def test_content_matching_keeps_subject_and_never_substitutes_another_library(self):
        cat=self.asset(description='一只小猫');self.asset('generated',name='狗',description='小狗')
        first=self.message('发一张贴吧猫猫图片')
        _,ctx=self.images.tools.context(first,[first])
        self.assertEqual([a['id'] for a in ctx['candidates']],[cat['id']])
        first=self.message('发一张贴吧狗狗图片',mid=2)
        _,ctx=self.images.tools.context(first,[first])
        self.assertEqual(ctx['candidates'],[])
        result=self.images.tools.execute(self.images.tools.direct_library_fallback(first,ctx),first,[first],ctx)
        self.assertEqual(result['code'],'no_match')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM picture_jobs').fetchone()[0],0)

    def test_generic_tieba_selection_prefers_latest_entry(self):
        first=self.asset(stamp=10)
        # Different encoded image content produces a distinct asset id.
        from PIL import Image
        import io
        out=io.BytesIO();Image.new('RGB',(21,21),'red').save(out,format='PNG');self.raw=out.getvalue()
        latest=self.asset(name='风景',description='山水',stamp=20)
        event=self.message('发张贴吧图')
        _,ctx=self.images.tools.context(event,[event])
        self.assertEqual(ctx['candidates'][0]['id'],latest['id'])
        self.assertNotEqual(first['id'],latest['id'])

    async def generate_with(self, handler):
        job=self.images.enqueue(kind='generate',prompt='蓝鲸',destination='library')
        original=httpx.AsyncClient
        with patch('whalegirl_linux.images.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)):
            await self.images.generate_once()
        return self.images.get(job)

    async def test_generated_result_download_retry_never_repeats_paid_submission(self):
        calls=[]
        def handler(req):
            calls.append(req.method)
            if req.method=='POST':return httpx.Response(200,json={'data':[{'url':'https://cdn.example.test/p.png'}]})
            if calls.count('GET')==1:raise httpx.ReadTimeout('synthetic')
            return httpx.Response(200,content=self.raw)
        job=await self.generate_with(handler)
        self.assertEqual(calls,['POST','GET','GET']);self.assertEqual(job['state'],'ready')
        self.assertEqual(json.loads(job['diagnostics_json'])['download_attempts'],2)

    async def test_post_timeout_notifies_uncertainty_and_never_resubmits(self):
        calls=[]
        def handler(req):calls.append(req.method);raise httpx.ReadTimeout('synthetic')
        job=await self.generate_with(handler)
        self.assertEqual(calls,['POST']);self.assertEqual(job['state'],'uncertain')
        self.assertEqual(json.loads(job['diagnostics_json'])['phase'],'submit')

    async def test_data_uri_base64_is_decoded_without_an_extra_generation(self):
        def handler(req):return httpx.Response(200,json={'data':[{'b64_json':'data:image/png;base64,\n'+base64.b64encode(self.raw).decode()}]})
        job=await self.generate_with(handler)
        self.assertEqual(job['state'],'ready')
        self.assertTrue(self.images.path(self.images.asset(job['asset_id'])).is_file())

    async def test_malformed_result_keeps_original_diagnostic_after_admin_review(self):
        def handler(req):return httpx.Response(200,json={'data':[{'b64_json':'broken!'}]})
        job=await self.generate_with(handler)
        self.assertEqual(job['state'],'uncertain')
        self.images.review(job['id'],False)
        reviewed=self.images.get(job['id'])
        self.assertEqual(json.loads(reviewed['diagnostics_json'])['original_error'],job['error'])
        self.assertEqual(reviewed['notice_state'],'reviewed')

    def failed_job(self):
        job=self.images.enqueue(kind='generate',prompt='蓝鲸',chat='private',destination='chat')
        self.images.change(job,state='failed',error='合成测试失败')
        self.gateway.status_for_send=self.gateway.status
        return job

    async def test_task_succeeds_while_notice_model_runs_no_false_message(self):
        job=self.failed_job()
        async def chat(*args,**kwargs):
            self.images.change(job,state='sent',receipt='actual',error='');return '这次没画出来'
        async def close():pass
        self.images.new_engine=lambda:SimpleNamespace(chat=chat,close=close)
        await self.images.notice_once()
        self.assertFalse(self.gateway.sent);self.assertEqual(self.images.get(job)['state'],'sent')

    async def test_task_succeeds_during_login_check_no_false_message(self):
        job=self.failed_job()
        async def chat(*args,**kwargs):return '这次没画出来'
        async def close():pass
        async def status():
            self.images.change(job,state='sent',receipt='actual',error='')
            return {'status':'logged_in','loggedInUser':'account'}
        self.images.new_engine=lambda:SimpleNamespace(chat=chat,close=close)
        self.gateway.status_for_send=status
        await self.images.notice_once()
        self.assertFalse(self.gateway.sent)

    async def test_real_current_failure_still_notifies_once(self):
        job=self.failed_job()
        async def chat(*args,**kwargs):return '这次图片任务没成功'
        async def close():pass
        self.images.new_engine=lambda:SimpleNamespace(chat=chat,close=close)
        await self.images.notice_once();await self.images.notice_once()
        self.assertEqual(len(self.gateway.sent),1);self.assertEqual(self.images.get(job)['notice_state'],'sent')

    async def test_pause_during_notice_generation_blocks_the_notification(self):
        job=self.failed_job()
        async def chat(*args,**kwargs):
            self.runtime.paused_reason='合成暂停';return '这次没画出来'
        async def close():pass
        self.images.new_engine=lambda:SimpleNamespace(chat=chat,close=close)
        await self.images.notice_once()
        self.assertFalse(self.gateway.sent);self.assertEqual(self.images.get(job)['notice_state'],'skipped')

    async def test_stale_failure_is_not_announced_in_a_new_conversation(self):
        job=self.failed_job()
        self.store.db.execute('UPDATE picture_jobs SET created=? WHERE id=?',(time.time()-3600,job))
        self.store.db.commit()
        self.images.new_engine=lambda: self.fail('No model call for an obsolete alert')
        await self.images.notice_once()
        self.assertFalse(self.gateway.sent);self.assertEqual(self.images.get(job)['notice_state'],'skipped')

    async def test_confirmed_image_survives_post_receipt_accounting_error(self):
        asset=self.asset('uploaded')
        self.config.save(self.config.settings.model_copy(update={'min_send_interval':0,'chat_cooldown':0}))
        job=self.images.enqueue(kind='send',asset_id=asset['id'],chat='private',destination='chat')
        async def send(*args):return Receipt(True,'actual')
        self.gateway.send_image=send;self.gateway.status_for_send=self.gateway.status
        with patch.object(self.runtime.safety,'outcome',side_effect=ValueError('synthetic accounting failure')):
            await self.images.process_once()
        self.assertEqual(self.images.get(job)['state'],'sent');self.assertIsNone(self.images.next_notice())

    async def test_late_server_ack_can_correct_exact_previously_failed_native_row(self):
        asset=self.asset('uploaded');job=self.images.enqueue(kind='send',asset_id=asset['id'],chat='private',destination='chat')
        event={'account':'account','chat':'private'}
        action=self.store.action_start('picture:'+job,event,0,{'type':'image','display':'[图片]'})
        self.store.action_finish(action,'failed',details={'asset_id':asset['id'],'before_ids':[],'local_id':9})
        self.images.change(job,state='failed',error='原生状态5')
        rows=[{'type':3,'isSelf':True,'localId':9,'serverId':'12345','nativeStatus':2,'timestamp':time.time()}]
        self.assertEqual(len(reconcile_outbound(self.store,'account','private',rows)),1)
        self.assertEqual(self.images.get(job)['state'],'sent');self.assertIsNone(self.images.next_notice())


if __name__=='__main__':
    unittest.main()
