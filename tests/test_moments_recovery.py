"""Recover definite failed daily slots and retain consistent character reference images."""
from datetime import datetime
import asyncio
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

from whalegirl_linux.moments import TZ,MomentsService
from whalegirl_linux.settings import Settings
from whalegirl_linux.store import Store
import test_moments_references as references


class ScheduleRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/'test-runs';root.mkdir(exist_ok=True)
        self.store=Store(Path(tempfile.mkdtemp(prefix='moments-recovery-',dir=root))/'state.sqlite3')
        self.settings=Settings(account_id='account',moments_enabled=True,moments_publish_enabled=True,
            moments_start_hour=0,moments_end_hour=0)
        self.runtime=SimpleNamespace(store=self.store,config=SimpleNamespace(settings=self.settings,generation=0),
            gateway=SimpleNamespace(ui_lock=asyncio.Lock(),inflight_sends=set()),
            gateway_state={'status':'logged_in'},account_id='account',processing='',paused_reason='',
            last_send=0,safety=SimpleNamespace(held=lambda account:''))
        self.service=MomentsService(self.runtime)

    def tearDown(self):self.store.close()

    def failed_day(self,*,hour=22,mode='auto',future=False):
        now=datetime.now(TZ).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
        self.start=now;self.now=now+hour*3600;self.day=datetime.fromtimestamp(now,TZ).strftime('%Y-%m-%d')
        self.runtime.config.settings=self.settings.model_copy(update={'moments_mode':mode,'moments_publish_per_day':3})
        slots=[{'index':i,'start':now+i*28800,'end':now+(i+1)*28800,'target':now+(4+8*i)*3600,
                'attempts':2 if i<2 else 1,'done':True} for i in range(3)]
        for i in (0,1):
            identity=self.service.book.create('account','publish',f'{self.day}/{i}/r2',{'auto_publish':True})
            self.service.book.change(identity,'failed',error='生成失败，未提交微信：AttributeError')
            self.store.db.execute('UPDATE moments_actions SET created=?,updated=? WHERE id=?',
                (now+(4+6*i)*3600,now+(4+6*i)*3600+10,identity))
        if future:
            slots[1].update(done=False,attempts=0,target=now+13*3600)
            slots[2].update(done=False,attempts=0,target=now+21*3600)
            # Slot 1 has not failed in this scenario.
            self.store.db.execute("UPDATE moments_actions SET source=source||'/unrelated' WHERE source=?",(f'{self.day}/1/r2',))
        else:
            identity=self.service.book.create('account','publish',f'{self.day}/2',{'auto_publish':True})
            self.service.book.change(identity,'sent',text='已发的不同内容',receipt='confirmed')
            self.store.db.execute('UPDATE moments_actions SET created=?,updated=? WHERE id=?',
                (now+21.5*3600,now+21.5*3600,identity))
        self.store.db.commit()
        return {'publish_plan':{'day':self.day,'per_day':3,'start_hour':0,'end_hour':0,
                'reservation_version':2,'slots':slots}}

    async def test_two_failed_slots_recover_without_consuming_success_quota(self):
        saved=self.failed_day();s=self.runtime.config.settings
        next_at=self.service.next_publish_time(s,saved,self.now)
        self.assertIsNotNone(next_at);self.assertGreater(next_at,self.now)
        recovery=[slot for slot in saved['publish_plan']['slots'] if 'recovery_for' in slot]
        self.assertEqual({slot['recovery_for'] for slot in recovery},{0,1})
        self.assertGreaterEqual(recovery[1]['target']-recovery[0]['target'],3600)
        self.assertTrue(all(slot['target']<self.start+86400 for slot in recovery))
        self.assertEqual(self.service.book.count('account','publish'),1)
        self.assertEqual(self.service.next_publish_time(s,saved,self.now),next_at)
        self.assertEqual(len(saved['publish_plan']['slots']),5)

    async def test_recovery_does_not_expire_unrelated_future_slot(self):
        saved=self.failed_day(hour=10,future=True);s=self.runtime.config.settings
        next_at=self.service.next_publish_time(s,saved,self.now)
        self.assertLess(next_at,self.start+13*3600)
        due=self.service.due_publish_slot(s,saved,next_at+1)
        self.assertEqual(due['recovery_for'],0)
        self.assertFalse(saved['publish_plan']['slots'][1]['done'])

    async def test_review_mode_does_not_create_autonomous_recovery(self):
        saved=self.failed_day(mode='review')
        self.assertIsNone(self.service.next_publish_time(self.runtime.config.settings,saved,self.now))
        self.assertEqual(len(saved['publish_plan']['slots']),3)

    async def test_uncertain_original_is_not_replayed(self):
        saved=self.failed_day()
        self.store.db.execute("UPDATE moments_actions SET state='uncertain' WHERE source=?",(f'{self.day}/0/r2',))
        self.store.db.commit()
        self.service.ensure_publish_plan(self.runtime.config.settings,saved,self.now)
        self.assertFalse(any(slot.get('recovery_for')==0 for slot in saved['publish_plan']['slots']))

    async def test_successful_daily_quota_prevents_new_recovery(self):
        saved=self.failed_day()
        for i in range(2):
            identity=self.service.book.create('account','publish','manual-'+str(i),{'auto_publish':True})
            self.service.book.change(identity,'sent',receipt='confirmed')
        self.assertIsNone(self.service.next_publish_time(self.runtime.config.settings,saved,self.now))
        self.assertEqual(len(saved['publish_plan']['slots']),3)


class GeneratedCharacterReferenceTests(unittest.IsolatedAsyncioTestCase):
    setUp=references.MomentsReferenceTests.setUp
    tearDown=references.MomentsReferenceTests.tearDown
    make_image=references.MomentsReferenceTests.make_image

    def generated(self,color,*,variant='girl',scope='global'):
        asset=self.service.save_asset(self.make_image(color),'generated',scope=scope,
            scope_chat='private' if scope=='chat' else '',character_id='whalegirl',character_variant=variant)
        self.store.db.execute("UPDATE picture_assets SET state='tagged' WHERE id=?",(asset['id'],))
        self.store.db.commit();return self.service.asset(asset['id'])

    async def test_use_same_variant_public_character_image_with_real_png_bytes(self):
        self.service.references.add(self.make_image(),'girl','少女版','角色外观')
        wanted=self.generated('green')
        self.generated('red',scope='chat');self.generated('yellow',variant='mature')
        identity=self.service.enqueue(kind='generate',prompt='在窗边看雨',destination='moments',moment_id='post')
        plan=json.loads(self.service.get(identity)['reference_json'])
        old=[item for item in plan['inputs'] if item['source']=='character_generated']
        self.assertEqual([item['asset_id'] for item in old],[wanted['id']]);self.assertEqual(plan['variant'],'girl')
        materialized=await self.service.references.materialize(plan,self.store,None,'account','')
        parts=self.service.references.upload_parts(materialized)
        self.assertEqual(parts[-1][1][1],self.service.path(wanted).read_bytes())
        self.assertIn('不能照搬',self.service.references.request_prompt('在窗边看雨',plan,materialized))
        self.assertIn('角色参考',self.service.retention.protection(wanted))

    async def test_private_or_other_variant_images_are_not_public_reference_fallbacks(self):
        self.service.references.add(self.make_image(),'girl','少女版','角色外观')
        self.generated('red',scope='chat');self.generated('yellow',variant='mature')
        identity=self.service.enqueue(kind='generate',prompt='新的日常',destination='moments',moment_id='post')
        plan=json.loads(self.service.get(identity)['reference_json'])
        self.assertTrue(plan['inputs']);self.assertTrue(all(item['source']=='character' for item in plan['inputs']))
