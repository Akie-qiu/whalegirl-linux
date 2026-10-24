"""Bounded checks for reduced UI work without weakening delivery receipts."""
import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from whalegirl_linux.gateway import AgentGateway

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'deploy' / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class SnapshotTests(unittest.TestCase):
    def test_compact_keeps_destination_and_editor_but_full_keeps_messages(self):
        safe = module('latency_a11y', 'a11y_safe.py')
        class Bus:
            reads = []
            def role(self, ref):
                return {safe.DESKTOP:'desktop-frame', 'chats':'list', 'messages':'list',
                        'editor':'text', 'row':'list-item', 'message':'list-item'}[ref]
            def properties(self, ref):
                return {'Name':{'chats':'Chats', 'messages':'Messages', 'row':'destination'}.get(ref,'')}
            def bounds(self, ref):return {'x':0,'y':0,'width':100,'height':50}
            def states(self, ref, role):return ['SELECTED'] if ref=='row' else ['EDITABLE'] if ref=='editor' else []
            def children(self, ref):
                self.reads.append(ref)
                return {safe.DESKTOP:['chats','messages','editor'], 'chats':['row'], 'messages':['message']}.get(ref,[])
        bus=Bus();compact,_=safe.dump_tree(bus,skip_messages=True)
        self.assertNotIn('messages',bus.reads)
        self.assertEqual(compact['children'][0]['children'][0]['states'],['SELECTED'])
        self.assertEqual(compact['children'][2]['states'],['EDITABLE'])
        full,_=safe.dump_tree(Bus())
        self.assertEqual(len(full['children'][1]['children']),1)

    def test_visible_native_sticker_needs_no_rescan_or_scroll(self):
        select=module('latency_select','chat_select_linux.py')
        tile={'role':'label','name':'Custom Stickers','bounds':{'x':10,'y':100,'width':64,'height':64}}
        root={'role':'frame','children':[
            {'role':'page-tab','name':'Custom Stickers'},
            {'role':'list','bounds':{'x':0,'y':80,'width':300,'height':300},'children':[tile]}]}
        runs=ROOT/'test-runs';runs.mkdir(exist_ok=True)
        path=Path(tempfile.mkdtemp(prefix='latency-',dir=runs))/'job.json'
        md5='a'*32;job={'md5':md5,'chat':'test'};commands=[]
        with patch.object(select,'native_read',return_value={'stickers':[{'md5':md5}]}), \
             patch.object(select,'selected',return_value=True), \
             patch.object(select,'tree',side_effect=AssertionError('Unnecessary UI rescan')), \
             patch.object(select.subprocess,'run',side_effect=lambda argv,**kw:commands.append(argv)):
            select.send_native_sticker(job,path,root,'test',True)
        self.assertEqual(job['state'],'submitted')
        self.assertEqual(len([argv for argv in commands if argv[0]=='/opt/tools/click']),1)
        self.assertFalse(any('--repeat' in argv for argv in commands))
        self.assertEqual(json.loads(path.read_text())['state'],'sending')


class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_simultaneous_checks_share_request_and_expired_send_status_refreshes(self):
        requests=[];account='first'
        async def handler(request):
            requests.append(request.url.path)
            await asyncio.sleep(.01)
            return httpx.Response(200,json={'status':'logged_in','loggedInUser':account})
        client=httpx.AsyncClient(base_url='http://127.0.0.1',transport=httpx.MockTransport(handler))
        gateway=AgentGateway('http://127.0.0.1','test',client=client)
        try:
            await asyncio.gather(gateway.status(),gateway.status(),gateway.status())
            self.assertEqual(len(requests),1)
            self.assertEqual((await gateway.status_for_send())['loggedInUser'],'first')
            self.assertEqual(len(requests),1)
            account='second';gateway._status_at-=3
            self.assertEqual((await gateway.status_for_send())['loggedInUser'],'second')
            self.assertEqual(len(requests),2)
        finally:await gateway.close()

    async def test_prefetch_is_reused_after_model_and_failure_is_not_cached(self):
        attempts=0
        async def handler(request):
            nonlocal attempts
            attempts+=1
            if attempts==1:return httpx.Response(503)
            return httpx.Response(200,json={'status':'logged_in','loggedInUser':'test'})
        gateway=AgentGateway('http://127.0.0.1','test',client=httpx.AsyncClient(
            base_url='http://127.0.0.1',transport=httpx.MockTransport(handler)))
        try:
            with self.assertRaises(httpx.HTTPStatusError):await gateway.status()
            gateway.warm_send_status()
            await asyncio.sleep(.01)
            self.assertEqual((await gateway.status_for_send())['loggedInUser'],'test')
            self.assertEqual(attempts,2)
        finally:await gateway.close()


if __name__=='__main__':unittest.main()
