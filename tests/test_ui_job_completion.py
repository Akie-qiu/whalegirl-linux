"""A native reset receipt is stronger than a later planner-exhaustion error."""
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import unquote
import httpx
from whalegirl_linux.gateway import AgentGateway,UIJobError


class CompletionTests(unittest.IsolatedAsyncioTestCase):
    async def run_job(self,operation,saved):
        runs=Path(__file__).resolve().parents[1]/'test-runs';runs.mkdir(exist_ok=True)
        folder=Path(tempfile.mkdtemp(prefix='ui-completion-',dir=runs))
        calls=[]
        def handler(request):
            calls.append(request)
            nonce=unquote(request.url.path).split('::whalejob::')[1].split('/')[0]
            path=folder/(nonce+'.json');value=json.loads(path.read_text());value.update(saved)
            path.write_text(json.dumps(value))
            return httpx.Response(200,json={'ok':False,'error':'No action selected'})
        async with httpx.AsyncClient(base_url='http://127.0.0.1:6175',transport=httpx.MockTransport(handler)) as client:
            gateway=AgentGateway('http://127.0.0.1:6175','fixture',client=client)
            gateway.ui_jobs_directory=folder
            try:return await gateway.ui_job(operation,'group@chatroom'),calls
            finally:await gateway.close()

    async def test_completed_reset_uses_receipt_without_another_ui_request(self):
        result,calls=await self.run_job('reset_ui',{'state':'recovered','main_ready':True})
        self.assertEqual(result['state'],'recovered');self.assertEqual(len(calls),1)
        self.assertIn('transport_warning',result)

    async def test_completed_open_chat_uses_its_done_receipt(self):
        result,calls=await self.run_job('open_chat',{'state':'done'})
        self.assertEqual(result['state'],'done');self.assertEqual(len(calls),1)

    async def test_failed_or_incomplete_reset_is_not_accepted(self):
        for saved in ({'state':'failed_before_send','error':'still blocked'},
                      {'state':'recovered','main_ready':False},{}):
            with self.subTest(saved=saved),self.assertRaises(UIJobError):await self.run_job('reset_ui',saved)

    async def test_receipt_must_match_the_requested_chat_and_operation(self):
        for saved in ({'state':'recovered','main_ready':True,'chat':'other@chatroom'},
                      {'state':'recovered','main_ready':True,'operation':'open_chat'}):
            with self.subTest(saved=saved),self.assertRaises(UIJobError):await self.run_job('reset_ui',saved)

    async def test_submitted_send_keeps_submission_state_for_db_receipt_check(self):
        result, calls = await self.run_job('send_text', {'state': 'submitted'})
        self.assertEqual(result['state'], 'submitted')
        self.assertEqual(result['transport_warning'], 'planner_exhausted_after_native_submission')
        self.assertEqual(len(calls), 1)

    async def test_unsubmitted_or_wrong_destination_send_is_not_accepted(self):
        for saved in ({'state': 'sending'}, {'state': 'submitted', 'chat': 'other'},
                      {'state': 'submitted', 'error': 'click failed'}):
            with self.subTest(saved=saved), self.assertRaises(UIJobError):
                await self.run_job('send_sticker', saved)


if __name__=='__main__':unittest.main()
