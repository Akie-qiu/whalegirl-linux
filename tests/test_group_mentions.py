import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import httpx
from whalegirl_linux.mentions import mention_options, apply_mentions, mention_prompt
from whalegirl_linux.conversation import plan_actions
from whalegirl_linux.gateway import AgentGateway
from whalegirl_linux.settings import Settings

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'deploy'))
import native_group_mentions as native


class MentionPlanTests(unittest.TestCase):
    def setUp(self):
        self.members=[{'id':'person-a','name':'小甲','aliases':['小甲']},
                      {'id':'person-b','name':'小乙','aliases':['小乙']}]
        self.options=mention_options(self.members,['person-b'])

    def test_known_ids_only_and_inline_names_become_native_targets(self):
        actions=plan_actions({'actions':[{'type':'text','text':'@小甲 来看看','mentions':['m1','unknown','m1']}]},{},Settings())
        action=apply_mentions(actions,self.options,True)[0]
        self.assertEqual({m['id'] for m in action['mentions']},{'person-a','person-b'})
        self.assertEqual(action['text'],'来看看')
        self.assertEqual(len(action['mentions']),2)

    def test_master_switch_drops_model_targets_and_does_not_fetch_private_people(self):
        action={'type':'text','text':'看看','display':'看看','mentions':['m1']}
        self.assertNotIn('mentions',apply_mentions([action],self.options,False)[0])
        self.assertIn('开关关闭',mention_prompt({},False))
        self.assertTrue(Settings().group_mentions_enabled)

    def test_duplicate_names_are_never_guessed(self):
        people=[{'id':'a','name':'同名','aliases':['同名']},{'id':'b','name':'同名','aliases':['同名']}]
        action={'type':'text','text':'@同名 来看看','display':'','mentions':[]}
        self.assertNotIn('mentions',apply_mentions([action],mention_options(people),True)[0])
        with self.assertRaisesRegex(ValueError,'名称重复'):native.unique_member_alias(people[0],people)

    def test_picker_ignores_chat_history_and_wrong_screen_area(self):
        row={'role':'list-item','name':'小甲','bounds':{'x':400,'y':510,'width':160,'height':32}}
        root={'children':[{'role':'list','name':'Messages','bounds':{'x':400,'y':500,'width':160,'height':100},'children':[row]},
                          {'role':'list','name':'','bounds':{'x':400,'y':500,'width':160,'height':100},'children':[row]}]}
        self.assertEqual(native.picker_rows(root,{'bounds':{'x':420,'y':650,'width':500,'height':40}}),[row])


class MentionGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_metadata_confirms_real_mention_without_replay(self):
        calls=[]
        async def handler(request):return httpx.Response(200,json=[])
        gateway=AgentGateway('http://127.0.0.1','test',client=httpx.AsyncClient(base_url='http://127.0.0.1',transport=httpx.MockTransport(handler)))
        gateway.ui_jobs_directory=Path('.')
        reads=0
        async def messages(chat):
            nonlocal reads
            reads+=1
            if reads<=2:return []
            return [{'localId':2,'serverId':123,'isSelf':True,'type':1,'content':'@小甲\u2005来看看',
                     'nativeStatus':2,'mentionedUserIds':['person-a']}]
        async def job(operation,chat,**kwargs):
            calls.append((operation,kwargs));return {'state':'submitted','mentions_applied':['person-a']}
        gateway.messages=messages;gateway.ui_job=job
        try:
            receipt=await gateway.send('g@chatroom','来看看',mentions=[{'id':'person-a','name':'小甲'}])
            self.assertTrue(receipt.confirmed)
            self.assertEqual(receipt.mentioned_ids,('person-a',))
            self.assertEqual(len(calls),1)
        finally:await gateway.close()

    async def test_roster_is_cached_per_group(self):
        gateway=AgentGateway('http://127.0.0.1','test');gateway.ui_jobs_directory=Path('.')
        calls=[]
        async def request(route,**kwargs):calls.append(kwargs['chat']);return {'members':[{'id':kwargs['chat'],'name':'test'}]}
        gateway.native_request=request
        try:
            await gateway.group_members('a@chatroom');await gateway.group_members('a@chatroom');await gateway.group_members('b@chatroom')
            self.assertEqual(calls,['a@chatroom','b@chatroom'])
        finally:await gateway.close()


if __name__=='__main__':unittest.main()
