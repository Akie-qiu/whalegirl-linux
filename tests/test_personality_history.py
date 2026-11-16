"""Admin lifecycle history is paginated, scoped, and retained across edits."""
import json
import time
import unittest

from fastapi.testclient import TestClient

import test_chat_habits as fixtures
from whalegirl_linux.app import create_app
from whalegirl_linux.memory_layers import INTERNET


class PersonalityHistoryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.ChatHabitTests.asyncSetUp
    asyncTearDown=fixtures.ChatHabitTests.asyncTearDown

    def revision(self,runtime,trait='interest:音乐',*,account=None,role=None,status='candidate'):
        before={'category':'interest','topic':'音乐','status':'candidate','support_count':1,'challenge_count':0,'source_count':1}
        after={**before,'status':status,'support_count':3,'source_count':2}
        with runtime.store.db:
            return runtime.store.db.execute('''INSERT INTO growth_personality_revisions
                (account,role_id,trait_key,before_json,after_json,reason,created) VALUES(?,?,?,?,?,?,?)''',
                (account or runtime.config.settings.account_id,role or runtime.memory_hub.role(),trait,
                 json.dumps(before,ensure_ascii=False),json.dumps(after,ensure_ascii=False),'合成证据：支持增加并跨越多次阅读',time.time())).lastrowid

    async def test_history_api_requires_auth_and_scopes_paginates_without_writes(self):
        app=create_app(self.folder/'history-console',gateway=fixtures.Gateway(),engine_factory=fixtures.Engine,run_background=False)
        cfg=app.state.config;cfg.save(self.config.settings,'test-key');runtime=app.state.runtime
        older=self.revision(runtime);newer=self.revision(runtime,status='stable')
        self.revision(runtime,account='other-account',status='stable')
        self.revision(runtime,role='other-role',status='stable')
        self.revision(runtime,trait='interest:绘画',status='stable')
        before_count=runtime.store.db.execute('SELECT count(*) FROM growth_personality_revisions').fetchone()[0]
        headers={'Authorization':'Bearer '+cfg.admin_token,'Origin':'http://127.0.0.1'}
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/growth/personality/revisions').status_code,401)
            response=client.get('/api/growth/personality/revisions',headers=headers,params={'trait_key':'interest:音乐','limit':1})
            self.assertEqual(response.status_code,200)
            page=response.json();self.assertEqual([row['id'] for row in page['items']],[newer]);self.assertEqual(page['next'],newer)
            current=page['items'][0]
            self.assertEqual(current['after']['status'],'stable');self.assertIn('跨越多次阅读',current['reason'])
            self.assertNotIn('after_json',current)
            page=client.get('/api/growth/personality/revisions',headers=headers,
                params={'trait_key':'interest:音乐','limit':1,'before':newer}).json()
            self.assertEqual([row['id'] for row in page['items']],[older]);self.assertIsNone(page['next'])
            self.assertEqual(client.get('/api/growth/personality/revisions',headers=headers,
                params={'trait_key':"' OR 1=1 --"}).json()['items'],[])
            self.assertEqual(runtime.store.db.execute('SELECT count(*) FROM growth_personality_revisions').fetchone()[0],before_count)

    async def test_lifecycle_history_survives_memory_lock_archive_and_restore(self):
        runtime=self.runtime;growth=runtime.growth;revision=self.revision(runtime,status='stable')
        row=runtime.memory_book.change('account',[INTERNET],{'chat':INTERNET,'subject':'self','kind':'persona',
            'content':'想继续了解音乐中的情绪转折','tier':'ordinary','venue':'any','source_key':''})
        with self.store.db:
            self.store.db.execute("UPDATE memories SET source_class='internet_learning' WHERE id=?",(row['id'],))
            self.store.db.execute('INSERT INTO growth_insights VALUES(?,?,?,?,?,?,?)',
                (row['id'],'synthetic-journal','interest','音乐','','',time.time()))
        growth.policy.register(row['id'],'tieba','interest',{'text':row['content']})
        locked=growth.edit(row['id'],{'version':row['version'],'locked':True})
        self.assertTrue(locked['locked'])
        archived=growth.edit(row['id'],{'op':'archive','version':locked['version']})
        restored=growth.edit(row['id'],{'op':'restore','version':archived['version']})
        self.assertEqual(restored['status'],'active')
        history=growth.personality_history('interest:音乐')
        self.assertEqual([item['id'] for item in history['items']],[revision])
        self.assertGreaterEqual(len(growth.revisions(row['id'])['memory']),4)


if __name__=='__main__':unittest.main()
