"""Dynamic model management and discovery against isolated stores/mock providers."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from whalegirl_linux.app import create_app
from whalegirl_linux.model_management import catalog_endpoint,fetch_models,request_key
from whalegirl_linux.settings import Config,ModelProfile,Settings
from whalegirl_linux.usage_pricing import ModelPricing,quote


def custom_price(**kwargs):
    return ModelPricing(mode='custom',input=2,output=8,cached=.5,write=3,**kwargs)


class DynamicProfileTests(unittest.TestCase):
    def test_nested_rates_never_alias_another_card_or_a_returned_copy(self):
        with tempfile.TemporaryDirectory() as raw:
            c=Config(Path(raw));p=ModelProfile(model='test-model',pricing=custom_price())
            first,_=c.create_model_profile(p);second,_=c.create_model_profile(p)
            p.pricing.input=99
            returned=c.model_profile(first);returned.pricing.input=88
            self.assertEqual(c.model_profile(first).pricing.input,2)
            self.assertEqual(c.model_profile(second).pricing.input,2)

    def test_named_profiles_prices_keys_and_active_model_survive_restart(self):
        with tempfile.TemporaryDirectory() as raw:
            folder=Path(raw);c=Config(folder);c.save(Settings(enabled=False),'original-key')
            identity,_=c.create_model_profile(ModelProfile(name='日常模型',api_url='https://first.invalid/v1',
                model='new-model',pricing=custom_price()),'new-key',activate=True)
            other,_=c.create_model_profile(ModelProfile(name='后台学习',model='study-model'),'study-key')
            c.save(c.settings.model_copy(update={'chat_cooldown':7,'growth_internet_model_profile':other}))
            loaded=Config(folder)
            self.assertEqual(loaded.active_model_profile,identity)
            self.assertEqual(loaded.settings.model,'new-model');self.assertEqual(loaded.settings.chat_cooldown,7)
            self.assertEqual(loaded.api_key(),'new-key');self.assertEqual(loaded.api_key(other),'study-key')
            self.assertEqual(loaded.api_key('custom'),'original-key')
            self.assertEqual(loaded.model_profile(identity).pricing,custom_price())
            self.assertEqual(loaded.model_profile(identity).name,'日常模型')
            serialized=json.loads((folder/'model-profiles.json').read_text(encoding='utf-8'))
            self.assertEqual(serialized['version'],2)
            self.assertNotIn('pricing',serialized['profiles']['custom'])
            self.assertEqual(serialized['metadata'][identity]['pricing']['input'],2)
            self.assertEqual(loaded.settings.growth_internet_model_profile,other)
            self.assertEqual(len(loaded.model_profiles_public()),6)
            self.assertNotIn('new-key',json.dumps(loaded.public()))

    def test_new_empty_card_never_borrows_existing_key(self):
        with tempfile.TemporaryDirectory() as raw:
            c=Config(Path(raw));c.save(Settings(enabled=False),'original-key')
            identity,_=c.create_model_profile(ModelProfile(name='无密钥'))
            self.assertEqual(c.api_key(identity),'');self.assertFalse(c.model_profiles_public()[identity]['api_key_configured'])
            self.assertEqual(c.active_model_profile,'custom')

    def test_changed_origin_does_not_receive_another_servers_key(self):
        with tempfile.TemporaryDirectory() as raw:
            c=Config(Path(raw));c.save(Settings(enabled=False),'secret-key')
            self.assertEqual(request_key(c,'custom','https://api.deepseek.com/chat/completions','openai'),'secret-key')
            with self.assertRaisesRegex(ValueError,'地址已变化'):
                request_key(c,'custom','https://another.invalid/v1','openai')
            self.assertEqual(request_key(c,'custom','https://another.invalid/v1','openai','new-key'),'new-key')

    def test_snapshot_uses_the_exact_card_and_is_immutable(self):
        from whalegirl_linux.upstream.ai_engine import AIEngine
        with tempfile.TemporaryDirectory() as raw:
            c=Config(Path(raw));c.save(Settings(enabled=False),'legacy')
            p=ModelProfile(name='测试费率',model='same-model',pricing=custom_price())
            a,_=c.create_model_profile(p,'a-key',activate=True);b,_=c.create_model_profile(
                p.model_copy(update={'pricing':ModelPricing(mode='custom',input=9,output=12)}),'b-key')
            url=AIEngine._normalize_api_url(p.api_url)
            snap=c.pricing_for_request(api_url=url,model='same-model',api_key='b-key',profile_id=b)
            self.assertEqual(snap['profile_id'],b);self.assertEqual(snap['pricing']['input'],9)
            c.save_model_profile(b,p,'',activate=False)
            self.assertEqual(snap['pricing']['input'],9)
            self.assertEqual(c.pricing_for_request(api_url=url,model='same-model',api_key='a-key')['profile_id'],a)
            self.assertEqual(c.pricing_for_request(api_url='https://wrong.invalid/v1/chat/completions',model='same-model',api_key='b-key'),{})

    def test_request_factory_records_custom_price_and_keeps_historical_snapshot(self):
        with tempfile.TemporaryDirectory() as raw:
            app=create_app(Path(raw),run_background=False)
            with TestClient(app):
                c=app.state.config;r=app.state.runtime
                identity,_=c.create_model_profile(ModelProfile(model='unit-model',pricing=custom_price()),'key',activate=True)
                engine=r.engine_factory(api_url=c.settings.api_url,api_key=c.api_key(),model=c.settings.model)
                engine.usage_callback=lambda event:r.token_ledger.record('account',event)
                engine.record_usage({'prompt_tokens':1000,'completion_tokens':200,
                    'prompt_tokens_details':{'cached_tokens':400},'completion_tokens_details':{'reasoning_tokens':100}},
                    started=time.monotonic(),created=time.time())
                saved=json.loads(r.store.db.execute('SELECT pricing_json FROM model_usage_context').fetchone()[0])
                self.assertEqual(saved['pricing_mode'],'custom');self.assertAlmostEqual(saved['low'],.003)
                r.config.settings=r.config.settings.model_copy(update={'account_id':'account'})
                summary=r.statistics.snapshot()['tokens']['summary']
                self.assertEqual(summary['pricing_modes'],{'custom':1})
                self.assertEqual(summary['official_costs'][0]['low'],.003)
                c.save_model_profile(identity,ModelProfile(model='unit-model',pricing=ModelPricing(mode='custom',input=20,output=80)),'',activate=True)
                again=json.loads(r.store.db.execute('SELECT pricing_json FROM model_usage_context').fetchone()[0])
                self.assertEqual(saved,again)


class PricingTests(unittest.TestCase):
    def event(self,**changes):
        return {'model':'custom-model','provider':'relay.invalid','operation':'chat','status':'success',
            'input_tokens':1_000_000,'output_tokens':100_000,'cached_tokens':400_000,'uncached_tokens':400_000,
            'cache_write_tokens':200_000,'reasoning_tokens':30_000,
            'pricing_snapshot':{'profile_id':'model-unit','name':'单元价格','pricing':custom_price().model_dump()},**changes}

    def test_cache_read_write_and_reasoning_are_not_double_counted(self):
        result=quote(self.event());self.assertAlmostEqual(result['low'],2.4);self.assertEqual(result['low'],result['high'])
        self.assertEqual(result['currency'],'CNY');self.assertEqual(result['pricing_mode'],'custom')
        self.assertEqual(result['version'],'custom-profile-v1');self.assertIsNone(result['verified_on'])

    def test_missing_differential_cache_rates_follow_explicit_input_fallback(self):
        price=ModelPricing(mode='custom',input=2,output=8).model_dump()
        r=quote(self.event(pricing_snapshot={'pricing':price}));self.assertAlmostEqual(r['low'],2.8)

    def test_unknown_usage_is_not_made_free_and_disabled_price_does_not_fall_back(self):
        self.assertIsNone(quote(self.event(input_tokens=None))['low'])
        r=quote(self.event(model='deepseek-flash',pricing_snapshot={'pricing':{'mode':'none'}}))
        self.assertIsNone(r['low']);self.assertEqual(r['pricing_mode'],'none')

    def test_unknown_cached_count_retains_range_and_zero_rates_are_valid(self):
        r=quote(self.event(cached_tokens=None,uncached_tokens=None));self.assertLess(r['low'],r['high'])
        p=ModelPricing(mode='custom',input=0,output=0,cached=0,write=0).model_dump()
        self.assertEqual(quote(self.event(pricing_snapshot={'pricing':p}))['low'],0)

    def test_invalid_or_nonfinite_prices_rejected(self):
        for updates in ({'input':-1},{'input':float('nan')},{'output':float('inf')},{'input':None}):
            with self.subTest(updates=updates),self.assertRaises(ValidationError):
                ModelPricing.model_validate({'mode':'custom','input':2,'output':8,**updates})


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    def test_endpoint_variants(self):
        for value,style,expected in [('https://relay.invalid/v1','openai','https://relay.invalid/v1/models'),
            ('https://relay.invalid/v1/responses','responses','https://relay.invalid/v1/models'),
            ('https://relay.invalid/api/v1/messages','anthropic','https://relay.invalid/api/v1/models'),
            ('https://api.deepseek.com/chat/completions','openai','https://api.deepseek.com/models')]:
            with self.subTest(value=value):self.assertEqual(catalog_endpoint(value,style),expected)

    async def test_models_are_deduplicated_and_provider_payload_is_not_returned(self):
        def respond(req):
            self.assertEqual(req.headers['authorization'],'Bearer secret')
            self.assertEqual(str(req.url),'https://relay.invalid/v1/models')
            return httpx.Response(200,json={'data':[{'id':'b','token':'do-not-return'},{'id':'a'},{'id':'b'},{'id':'x'*101}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            r=await fetch_models('https://relay.invalid/v1','responses','secret',client=client)
        self.assertEqual(r['models'],[{'id':'a'},{'id':'b'}]);self.assertNotIn('do-not-return',json.dumps(r))

    async def test_anthropic_pagination_and_headers(self):
        seen=[]
        def respond(req):
            seen.append(req);self.assertEqual(req.headers['x-api-key'],'secret');self.assertNotIn('authorization',req.headers)
            self.assertEqual(req.headers['anthropic-version'],'2023-06-01')
            return httpx.Response(200,json={'data':[{'id':'one' if len(seen)==1 else 'two'}],
                'has_more':len(seen)==1,'last_id':'one'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            r=await fetch_models('https://provider.invalid/v1','anthropic','secret',client=client)
        self.assertEqual(len(seen),2);self.assertEqual(seen[1].url.params['after_id'],'one');self.assertFalse(r['partial'])

    async def test_error_and_redirect_never_echo_secrets_or_follow_location(self):
        for status in (401,403,404,429,500,302):
            seen=[]
            def respond(req):
                seen.append(req);return httpx.Response(status,json={'error':'secret'},headers={'Location':'https://untrusted.invalid'})
            with self.subTest(status=status):
                async with httpx.AsyncClient(transport=httpx.MockTransport(respond),follow_redirects=True) as client:
                    with self.assertRaises(ValueError) as error:await fetch_models('https://provider.invalid/v1','openai','secret',client=client)
                self.assertNotIn('secret',str(error.exception));self.assertEqual(len(seen),1)

    async def test_malformed_and_empty_lists_keep_manual_entry_available(self):
        for response in (httpx.Response(200,text='<html>wrong service</html>'),httpx.Response(200,json={'data':[]})):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:response)) as client:
                with self.assertRaisesRegex(ValueError,'手动填写'):
                    await fetch_models('https://provider.invalid/v1','openai','key',client=client)


class ModelApiTests(unittest.TestCase):
    def test_create_copy_edit_and_discovery_do_not_switch_until_requested(self):
        with tempfile.TemporaryDirectory() as raw:
            app=create_app(Path(raw),run_background=False)
            with TestClient(app) as client:
                c=app.state.config;c.save(Settings(enabled=False),'original-key')
                h={'Authorization':'Bearer '+c.admin_token}
                body={'profile':{'name':'备用接口','model':'custom-model','api_url':'https://provider.invalid/v1',
                    'pricing':{'mode':'custom','currency':'USD','input':1,'output':4}},'api_key':'secret-key'}
                r=client.post('/api/model-profiles/create',headers=h,json=body)
                self.assertEqual(r.status_code,200,r.text);identity=r.json()['profile_id']
                self.assertEqual(r.json()['config']['active_model_profile'],'custom');self.assertNotIn('secret-key',r.text)
                with patch('whalegirl_linux.app.fetch_models',new=AsyncMock(return_value={'models':[{'id':'custom-model'}],'count':1})) as fetch:
                    r=client.post('/api/model-profiles/models',headers=h,json={'profile_id':identity,'api_url':'https://provider.invalid/v1'})
                self.assertEqual(r.status_code,200);self.assertEqual(fetch.call_args.args[-1],'secret-key')
                self.assertEqual(c.active_model_profile,'custom')
                copied=client.post('/api/model-profiles/create',headers=h,json={'profile':body['profile'],
                    'copy_from':identity,'copy_key':True}).json()['profile_id']
                self.assertEqual(c.api_key(copied),'secret-key')
                r=client.post('/api/model-profiles',headers=h,json={'profile_id':identity,'profile':body['profile'],'activate':True})
                self.assertEqual(r.status_code,200);self.assertEqual(c.active_model_profile,identity)
                self.assertEqual(c.api_key('custom'),'original-key')
                self.assertEqual(client.post('/api/model-profiles/models',json={'api_url':'https://provider.invalid'}).status_code,401)
                self.assertEqual(client.get('/model-manager.js').status_code,200)
                self.assertEqual(client.get('/model-manager.css').status_code,200)

    def test_test_draft_uses_draft_values_without_saving_or_activating(self):
        received=[]
        class Engine:
            api_style='openai';last_error=''
            def __init__(self,**kwargs):received.append(kwargs)
            async def chat(self,*args,**kwargs):return '连接正常'
            async def close(self):pass
        with tempfile.TemporaryDirectory() as raw:
            app=create_app(Path(raw),run_background=False,engine_factory=Engine)
            with TestClient(app) as client:
                c=app.state.config;before=c.public();h={'Authorization':'Bearer '+c.admin_token}
                r=client.post('/api/model-profiles/test',headers=h,json={'profile':{
                    'name':'未保存模型','api_url':'https://draft.invalid/v1','model':'draft-model',
                    'model_temperature':1.2,'model_stream_enabled':True},'api_key':'draft-key'})
                self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['ok'])
                self.assertEqual(c.public(),before)
                self.assertEqual(received[-1]['temperature'],1.2);self.assertEqual(received[-1]['stream_enabled'],True)
                self.assertNotIn('model_temperature',received[-1]);self.assertNotIn('pricing',received[-1])


if __name__=='__main__':unittest.main()
