"""Life audit windows remain bounded without losing durable event history."""
import json
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import test_chat_habits as fixtures
from whalegirl_linux.app import create_app


class LifeEventHistoryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.ChatHabitTests.asyncSetUp
    asyncTearDown=fixtures.ChatHabitTests.asyncTearDown

    def seed(self,runtime,count=300):
        service=runtime.life_autonomy
        change=service.preview('growth_exploration',runtime.config.settings.growth_exploration+1,
            '合成长期事务事件，验证分页不会删除历史')
        with runtime.store.db:
            runtime.store.db.executemany('''INSERT INTO life_parameter_events(change_id,stage,details,created)
                VALUES(?,?,?,?)''',[(change['id'],'synthetic_observation',json.dumps({'sequence':index}),time.time()+index)
                for index in range(1,count)])
        return change['id']

    @staticmethod
    def snapshot(runtime):
        return [tuple(row) for row in runtime.store.db.execute('SELECT * FROM life_parameter_events ORDER BY id')]

    async def test_catalog_loads_recent_40_of_300_and_keeps_every_stored_event(self):
        runtime=self.runtime;identity=self.seed(runtime);before=self.snapshot(runtime)
        row=runtime.life_autonomy.catalog()['transactions'][0]
        self.assertEqual(row['id'],identity)
        self.assertEqual(row['event_total'],300)
        self.assertTrue(row['events_truncated'])
        self.assertEqual(len(row['events']),40)
        self.assertEqual([event['id'] for event in row['events']],[event[0] for event in before[-40:]])
        self.assertEqual(row['events_next'],before[-40][0])
        self.assertEqual(row['events'][-1]['details'],{'sequence':299})
        self.assertEqual(self.snapshot(runtime),before)

    async def test_cursor_covers_all_300_with_no_duplicates_or_scope_leak_or_deletion(self):
        runtime=self.runtime;service=runtime.life_autonomy;identity=self.seed(runtime)
        before=self.snapshot(runtime);cursor=0;seen=[];pages=0
        while True:
            page=service.event_page(identity,cursor,1000)
            self.assertLessEqual(len(page['items']),40)
            self.assertEqual(page['event_total'],300)
            seen.extend(event['id'] for event in page['items']);pages+=1
            if page['next'] is None:break
            self.assertTrue(page['has_more']);cursor=page['next']
        self.assertEqual(pages,8)
        self.assertEqual(len(seen),300)
        self.assertEqual(len(set(seen)),300)
        self.assertEqual(sorted(seen),[event[0] for event in before])
        account,role=service._scope()
        for wrong_scope in (('other-account',role),(account,'other-role')):
            with self.subTest(scope=wrong_scope),patch.object(service,'_scope',return_value=wrong_scope):
                self.assertEqual(service.history(),[])
                with self.assertRaisesRegex(ValueError,'当前角色'):
                    service.event_page(identity)
        self.assertEqual(self.snapshot(runtime),before)

    async def test_behavior_counts_300_but_decodes_only_latest_5_matching_events(self):
        runtime=self.runtime;service=runtime.life_autonomy;parameter='growth_exploration'
        value=runtime.config.settings.growth_exploration+1
        change=service.propose_and_apply(parameter,value,'合成长期行为证据窗口',actor='admin')
        row=dict(runtime.store.db.execute('SELECT * FROM life_parameter_changes WHERE id=?',(change['id'],)).fetchone())
        applied=row['applied_at']
        details=lambda sequence,**extra:json.dumps({'parameter':parameter,'value':value,'sequence':sequence,**extra})
        with runtime.store.db:
            runtime.store.db.executemany('INSERT INTO life_parameter_events(change_id,stage,details,created) VALUES(?,?,?,?)',
                [(change['id'],'behavior_observed',details(index),applied+index+1) for index in range(300)]+[
                    (change['id'],'behavior_observed','{invalid-json',applied+301),
                    (change['id'],'behavior_observed',details(300,parameter='bilibili_interval_minutes'),applied+301),
                    (change['id'],'behavior_observed',details(300,value=value+1),applied+301),
                    (change['id'],'behavior_observed',details(300),applied-1)])
        before=self.snapshot(runtime)
        with patch('whalegirl_linux.life_autonomy.json.loads',wraps=json.loads) as decode:
            behavior=service._behavior_after(row)
            self.assertEqual(decode.call_count,5)
        self.assertEqual(behavior['count'],300)
        self.assertTrue(behavior['verified'])
        self.assertEqual([item['sequence'] for item in behavior['evidence']],list(range(295,300)))
        self.assertEqual(self.snapshot(runtime),before)

    async def test_event_api_auth_scope_cursor_bound_and_zero_writes(self):
        app=create_app(self.folder/'life-history-console',gateway=fixtures.Gateway(),
            engine_factory=fixtures.Engine,run_background=False)
        cfg=app.state.config;cfg.save(self.config.settings,'test-key');runtime=app.state.runtime
        identity=self.seed(runtime);service=runtime.life_autonomy;account,role=service._scope()
        hidden=[]
        for scope in (('other-account',role),(account,'other-role')):
            with patch.object(service,'_scope',return_value=scope):
                hidden.append(service.preview('growth_exploration',cfg.settings.growth_exploration+1)['id'])
        before=self.snapshot(runtime);headers={'Authorization':'Bearer '+cfg.admin_token,'Origin':'http://127.0.0.1'}
        route='/api/life-autonomy/'+identity+'/events'
        with TestClient(app) as client:
            self.assertEqual(client.get(route).status_code,401)
            response=client.get(route,headers=headers,params={'limit':10000})
            self.assertEqual(response.status_code,200)
            page=response.json();self.assertEqual(len(page['items']),40)
            self.assertEqual(page['event_total'],300);self.assertTrue(page['events_truncated'])
            older=client.get(route,headers=headers,params={'before':page['next'],'limit':40}).json()
            self.assertEqual(len(older['items']),40)
            self.assertLess(older['items'][-1]['id'],page['items'][0]['id'])
            for identity in hidden:
                self.assertEqual(client.get('/api/life-autonomy/'+identity+'/events',headers=headers).status_code,404)
            self.assertEqual(client.get('/api/life-autonomy/missing/events',headers=headers).status_code,404)
            self.assertEqual(client.get(route,headers=headers,params={'before':'not-an-integer'}).status_code,422)
            self.assertEqual(self.snapshot(runtime),before)


if __name__=='__main__':unittest.main()
