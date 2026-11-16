"""Budget, provenance and exact error states using synthetic frames only."""
from types import SimpleNamespace
import unittest

from whalegirl_linux.bilibili import BilibiliService
from whalegirl_linux.bilibili_vision import understand,visual_budget
from whalegirl_linux.bilibili_client import BiliError
from whalegirl_linux.growth import GrowthStopped


class FakeEngine:
    def __init__(self):
        self.timeout=60;self.last_error='';self.last_response_meta={};self.closed=False
    async def close(self):self.closed=True


class FakeGrowth:
    def __init__(self,result=None,error=None):
        self.result=result or {};self.error=error;self.engine=FakeEngine()
        self.calls=[];self.events=[];self.usage=[];self.worker_kinds=[]
    def worker_engine(self,kind):self.worker_kinds.append(kind);return self.engine
    def event(self,*args,**kwargs):self.events.append((args,kwargs))
    def record_usage(self,event):self.usage.append(event)
    async def model_call(self,engine,system,data,generation,role,**options):
        self.calls.append((system,data,options))
        engine.last_response_meta={'finish_reason':'length' if self.error=='length' else 'stop',
                                  'output_budget':options['max_tokens']+1024,'attempts':1}
        engine.usage_callback({'input_tokens':100,'output_tokens':40,'total_tokens':140,'reasoning_tokens':30})
        if self.error=='length':
            engine.last_error='length SYNTHETIC_SECRET_ECHO';raise ValueError(engine.last_error)
        if self.error=='http':
            engine.last_response_meta={'http_status':429,'attempts':1};engine.last_error='HTTP 429'
            raise ValueError('HTTP 429')
        if self.error=='cancel':raise GrowthStopped('context changed')
        return self.result


def material(count=16):
    frames=[{'time':300+i*5,'jpeg':b'fixture'} for i in range(count)]
    return {'frames':frames,'boards':[{'jpeg':b'fixture board','cells':[
        {'number':i+1,'time':frame['time']} for i,frame in enumerate(frames)]}],
        'segments':[{'start':299,'end':306,'text':'合成字幕'}],'title':'合成视频','part':'第一段',
        'coverage':{'notes':[],'complete_playback':False,'visual_understood':False}}


class VisionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):self.service=SimpleNamespace(config=SimpleNamespace(settings=SimpleNamespace(vision_enabled=True)))

    def test_budget_scales_for_real_frame_count_and_remains_bounded(self):
        self.assertEqual(visual_budget(6),4096)
        self.assertEqual(visual_budget(16),5120)
        self.assertEqual(visual_budget(24),7168)
        self.assertEqual(visual_budget(100),7168)

    async def test_one_call_keeps_exact_frame_time_and_reported_usage(self):
        data=material();growth=FakeGrowth({'frames':[{'number':2,'description':'两个人站在道路边看向前方'}]})
        result=await understand(self.service,data,growth,0,'role')
        self.assertEqual(result[0]['time'],305)
        self.assertTrue(data['coverage']['visual_understood'])
        self.assertFalse(data['coverage']['complete_playback'])
        self.assertEqual(data['coverage']['visual_cells_reviewed'],[2])
        analysis=data['coverage']['visual_analysis']
        self.assertEqual(analysis['state'],'completed')
        self.assertEqual(analysis['response']['output_budget'],6144)
        self.assertEqual(analysis['reported_tokens']['total_tokens'],140)
        self.assertEqual(analysis['reported_tokens']['reasoning_tokens'],30)
        self.assertEqual(analysis['model_requests'],1)
        self.assertEqual(growth.calls[0][2]['max_tokens'],5120)
        self.assertEqual(growth.engine.timeout,90)
        self.assertTrue(growth.engine.closed)
        self.assertEqual(growth.worker_kinds,['bilibili'])

    async def test_length_is_persisted_without_raw_echo_or_silent_second_request(self):
        data=material();data['coverage']['visual_understood']=True;growth=FakeGrowth(error='length')
        self.assertEqual(await understand(self.service,data,growth,0,'role'),'')
        self.assertFalse(data['coverage']['visual_understood'])
        self.assertEqual(data['coverage']['visual_analysis']['reason'],'length')
        self.assertIn('length',str(data['coverage']['notes']))
        self.assertNotIn('SYNTHETIC_SECRET',str(data['coverage']))
        self.assertEqual(len(growth.calls),1)
        self.assertEqual(len(growth.usage),1)
        self.assertTrue(growth.engine.closed)

    async def test_model_rate_limit_does_not_retry_or_become_a_watch_success(self):
        data=material();growth=FakeGrowth(error='http')
        await understand(self.service,data,growth,0,'role')
        self.assertEqual(data['coverage']['visual_analysis']['reason'],'http_429')
        self.assertFalse(data['coverage']['visual_understood'])
        self.assertEqual(len(growth.calls),1)

    async def test_context_cancellation_propagates_and_closes_visual_client(self):
        data=material();growth=FakeGrowth(error='cancel')
        with self.assertRaises(GrowthStopped):await understand(self.service,data,growth,0,'role')
        self.assertEqual(data['coverage']['visual_analysis']['state'],'cancelled')
        self.assertTrue(growth.engine.closed)

    async def test_fake_duplicate_unclear_and_out_of_range_cells_are_not_claimed(self):
        data=material(6);growth=FakeGrowth({'frames':[
            {'number':True,'description':'布尔值不能冒充格号'},
            {'number':8,'description':'没有展示的画面不能通过'},
            {'number':2,'description':'画面实际看不清楚','unclear':True},
            {'number':3,'description':'路边停着一辆白色的车'},
            {'number':3,'description':'重复格号不能多计一次'}]})
        rows=await understand(self.service,data,growth,0,'role')
        self.assertEqual([r['number'] for r in rows],[3])
        self.assertEqual(data['coverage']['visual_analysis']['reviewed_frames'],1)

    async def test_empty_observations_do_not_count_as_visual_understanding(self):
        data=material();growth=FakeGrowth({})
        self.assertEqual(await understand(self.service,data,growth,0,'role'),[])
        self.assertFalse(data['coverage']['visual_understood'])
        self.assertEqual(data['coverage']['visual_analysis']['state'],'no_observations')

    async def test_visual_outcome_does_not_prematurely_finish_the_parent_growth_task(self):
        for growth in (FakeGrowth({'frames':[{'number':1,'description':'窗边摆着一张木头桌子'}]}),
                       FakeGrowth({}),FakeGrowth(error='length')):
            await understand(self.service,material(),growth,0,'role')
            outcomes=[(args,options) for args,options in growth.events if args[0]=='vision_result']
            self.assertEqual(len(outcomes),1)
            self.assertNotIn('state',outcomes[0][1])
            self.assertIn('state',outcomes[0][0][2])

    async def test_source_conflict_remains_a_hard_stop(self):
        data=material();growth=FakeGrowth({'source_conflict':True})
        with self.assertRaises(BiliError):await understand(self.service,data,growth,0,'role')
        self.assertEqual(data['coverage']['visual_analysis']['reason'],'source_mismatch')

    async def test_disabled_or_oversized_material_never_creates_a_paid_call(self):
        for data in (material(25),{**material(),'frames':[]}):
            growth=FakeGrowth()
            await understand(self.service,data,growth,0,'role')
            self.assertFalse(growth.calls)
            self.assertFalse(growth.worker_kinds)

    async def test_existing_bilibili_entry_uses_visual_helper_without_changing_chat_engine(self):
        service=BilibiliService.__new__(BilibiliService);service.config=self.service.config
        growth=FakeGrowth({'frames':[{'number':1,'description':'蓝色天空下有一条公路'}]})
        chat_engine=FakeEngine()
        await service.visual(material(),growth,chat_engine,0,'role')
        self.assertEqual(chat_engine.timeout,60)
        self.assertFalse(chat_engine.closed)


if __name__=='__main__':unittest.main()
