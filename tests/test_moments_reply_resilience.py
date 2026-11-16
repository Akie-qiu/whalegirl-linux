"""Rich comments, resized layouts and bounded pre-submit recovery; no real sends."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'deploy'))
import moments_layout
import moments_ui
from whalegirl_linux.gateway import UIJobError
from whalegirl_linux.moments import MomentsService
from whalegirl_linux.moments_payload import parse_post
from tests import test_moments as fixtures


class RichCommentTests(unittest.TestCase):
    def test_native_attachment_counts_without_credentials_or_media_urls(self):
        raw='''<SnsDataItem><TimelineObject><id>17</id><username>bot</username></TimelineObject>
            <LocalExtraInfo><user_comment><comment_id>1</comment_id><username>friend</username>
            <content>一张照片</content><comment_imageinfo_count>1</comment_imageinfo_count>
            <imagelist><imageinfo><url>https://private.invalid/image</url><token>secret-token</token>
            <key>secret-key</key></imageinfo></imagelist></user_comment></LocalExtraInfo></SnsDataItem>'''
        c=parse_post({'content':raw},{'bot'})['comments'][0]
        self.assertEqual(c['image_count'],1)
        self.assertEqual(c['text'],'一张照片')
        self.assertNotIn('secret',json.dumps(c));self.assertNotIn('private.invalid',json.dumps(c))

    def test_declared_attachment_without_actual_image_metadata_is_not_trusted(self):
        raw='''<SnsDataItem><TimelineObject><id>17</id></TimelineObject><LocalExtraInfo>
            <user_comment><comment_id>1</comment_id><content>测试</content>
            <comment_imageinfo_count>1</comment_imageinfo_count></user_comment></LocalExtraInfo></SnsDataItem>'''
        self.assertEqual(parse_post({'content':raw})['comments'][0]['image_count'],0)

    def test_image_suffix_matches_only_real_native_image_count(self):
        c={'text':'收到新照片','name':'朋友','image_count':1}
        for text in ('收到新照片[Photo]','收到新照片[Image]','收到新照片[图片]',
                     '朋友：收到新照片[Photo]','朋友：收到新照片[Image]','收到新照片'):
            with self.subTest(text=text):self.assertTrue(moments_ui.copied_comment_matches(text,c))
        for text in ('收到新照片[Smile]','收到新照片[Image]额外一句','别人的收到新照片[Image]',
                     '收到新照片[Image][Image]'):
            with self.subTest(text=text):self.assertFalse(moments_ui.copied_comment_matches(text,c))
        self.assertFalse(moments_ui.copied_comment_matches('收到新照片[Image]',{**c,'image_count':0}))
        self.assertFalse(moments_ui.copied_comment_matches('收到新照片[Image]',{**c,'image_count':True}))

    def test_older_draft_uses_fresh_canonical_attachment_metadata(self):
        old={'id':'1','author':'friend','text':'配图评论','created':1}
        current={**old,'image_count':1}
        post={'id':'p','comments':[current]}
        reply={'role':'menu-item','name':'Reply'}
        with patch.object(moments_ui,'select_timeline_comment',return_value=(100,100)) as select,\
             patch.object(moments_ui,'native_read',return_value={'posts':[post]}),\
             patch.object(moments_ui,'tree',return_value={'children':[reply]}),\
             patch.object(moments_ui,'activate_moments'),patch.object(moments_ui,'command'),\
             patch.object(moments_ui,'click_node'):
            moments_ui.reply_comment(post,{'comment':old})
        self.assertIs(select.call_args.args[1],current)

    def test_native_reply_and_friend_comment_are_checked_for_duplicates(self):
        c={'id':'1','author':'friend','text':'原评论'}
        post={'id':'p','author':'bot','text':'原动态','own':True,'comments':[c,
            {'id':'2','own':True,'local':False,'reply_to':'1'}]}
        with self.assertRaisesRegex(ValueError,'already has a native reply'):
            moments_ui.matching_post({'posts':[post]},{'post':post,'comment':c},'reply_own')
        post={**post,'own':False}
        with self.assertRaisesRegex(ValueError,'already has a native comment'):
            moments_ui.matching_post({'posts':[post]},{'post':post},'comment_friend')

    def test_comment_deleted_after_selection_is_rejected(self):
        c={'id':'1','author':'friend','text':'原评论'}
        post={'id':'p','author':'bot','text':'原动态','own':True,'comments':[]}
        with self.assertRaisesRegex(ValueError,'no longer available'):
            moments_ui.matching_post({'posts':[post]},{'post':post,'comment':c},'reply_own')

    def test_reply_arriving_during_composition_blocks_the_actual_submit(self):
        import moments_session
        (ROOT/'test-runs').mkdir(exist_ok=True)
        c={'id':'1','author':'friend','text':'原评论','created':1}
        post={'id':'p','author':'bot','text':'原动态','own':True,'comments':[c]}
        fresh={**post,'comments':[c,{'id':'2','own':True,'local':False,'reply_to':'1'}]}
        session=SimpleNamespace(recover=lambda:None,can_close=lambda:False,closed=lambda:None,
            submitting=unittest.mock.Mock())
        with tempfile.TemporaryDirectory(dir=ROOT/'test-runs') as folder:
            path=Path(folder)/'job.json';path.write_text(json.dumps({'created':time.time(),
                'operation':'moments_send','kind':'reply_own','text':'测试回复',
                'source':{'post':post,'comment':c}}))
            with patch.object(moments_ui,'authorize',side_effect=[({'posts':[post]},{}),({'posts':[fresh]}, {})]),\
                 patch.object(moments_session,'ComposerSession',return_value=session),\
                 patch.object(moments_ui,'tree',return_value={}),\
                 patch.object(moments_ui,'open_moments'),patch.object(moments_ui,'close_moments'),\
                 patch.object(moments_ui,'comment_editor',return_value={'native_comment':True}),\
                 patch.object(moments_ui,'put_comment_text',return_value=(200,300)),\
                 patch.object(moments_ui,'command') as command,patch.object(moments_ui,'click_node') as click:
                with self.assertRaisesRegex(ValueError,'already has a native reply'):
                    moments_ui.run(path,'bot')
            command.assert_not_called();click.assert_not_called();session.submitting.assert_not_called()


class ResizedLayoutTests(unittest.TestCase):
    def test_text_lines_and_dividers_follow_actual_width(self):
        (ROOT/'test-runs').mkdir(exist_ok=True)
        for width in (438,548,640,800):
            with self.subTest(width=width),tempfile.TemporaryDirectory(dir=ROOT/'test-runs') as folder:
                path=Path(folder)/'comments.png'
                image=Image.new('RGB',(width,139),(247,247,247));draw=ImageDraw.Draw(image)
                draw.line((width//2,60,width-15,60),fill=(230,230,230))
                for top in (12,82):draw.rectangle((20,top,180,top+10),fill=(80,90,120))
                image.save(path)
                self.assertEqual(moments_layout.comment_bands(path,1),[(61,139)])
                self.assertEqual(moments_layout.timeline_comment_points(path),[17,87])

    def test_offscreen_binding_requires_current_viewport_width(self):
        for width in (438,640):
            row={'role':'list-item','bounds':{'x':0,'y':-25,'width':width,'height':542}}
            f={'role':'frame','name':'Moments','children':[{'role':'list',
                'bounds':{'x':0,'y':40,'width':width,'height':477},'children':[row]}]}
            with self.subTest(width=width),patch.object(moments_ui,'frame',return_value=f):
                self.assertEqual(moments_ui.comment_detail({'text':'source'},542)[0]['width'],width)
                row['bounds']['width']=width-30
                with self.assertRaisesRegex(ValueError,'not confirmed'):
                    moments_ui.comment_detail({'text':'source'},542)

    def test_tiny_or_oversized_capture_is_rejected(self):
        for width in (1,200,2049):
            with self.subTest(width=width),self.assertRaises(ValueError):
                moments_layout.comment_dividers(width,[bytearray(width*3)])


class ReplyRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        (ROOT/'test-runs').mkdir(exist_ok=True)
        fixtures.MomentsTests.setUp(self)
        self.post=fixtures.MomentsTests.cached_comments(self)
        self.service.generate=AsyncMock(side_effect=AssertionError('Recovery must not call a model'))
        self.identity=self.service.book.create('account','reply_own','post/1',
            {'post':self.post,'comment':self.post['comments'][0]})
        self.service.book.change(self.identity,'draft',text='保留同一条回复')
    tearDown=fixtures.MomentsTests.tearDown

    async def fail_once(self,error=None):
        self.runtime.gateway.moments=AsyncMock(side_effect=error or UIJobError(
            'Timeline: Exact source comment is not present in visible native history','failed_before_send'))
        return await self.service.send(self.identity)

    def make_due(self,*,expired=False):
        row=self.service.book.get('account',self.identity);payload=json.loads(row['payload'])
        payload['reply_retry']['next_at']=time.time()-1
        if expired:payload['reply_retry']['expires_at']=time.time()-1
        self.service.book.change(self.identity,'failed',text=row['text'],payload=payload)

    async def test_definite_pre_submit_failure_resumes_same_text_without_model(self):
        row=await self.fail_once();meta=json.loads(row['payload'])['reply_retry']
        self.assertEqual(meta['status'],'pending');self.assertEqual(meta['failures'],1)
        self.make_due()
        self.runtime.gateway.moments=AsyncMock(return_value={'confirmed':True,'receipt':'native-id'})
        await self.service.tick()
        row=self.service.book.get('account',self.identity)
        self.assertEqual(row['state'],'sent');self.assertEqual(row['text'],'保留同一条回复')
        self.assertEqual(json.loads(row['payload'])['reply_retry']['status'],'resolved')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM moments_actions').fetchone()[0],1)
        self.service.generate.assert_not_called()

    async def test_backoff_and_three_total_failures_are_bounded(self):
        row=await self.fail_once()
        self.assertIsNone(await self.service.resume_reply_retry(self.identity))
        for failures in (2,3):
            self.make_due();self.assertIsNotNone(await self.service.resume_reply_retry(self.identity))
            row=await self.service.send(self.identity)
            meta=json.loads(row['payload'])['reply_retry'];self.assertEqual(meta['failures'],failures)
        self.assertEqual(meta['status'],'exhausted');self.assertEqual(meta['next_at'],0)
        self.assertIsNone(await self.service.resume_reply_retry(self.identity))
        self.assertEqual(self.runtime.gateway.moments.await_count,3)

    async def test_retry_survives_service_recreation_without_cloning(self):
        await self.fail_once();self.make_due();self.service=MomentsService(self.runtime)
        row=await self.service.resume_reply_retry(self.identity)
        self.assertEqual(row['id'],self.identity);self.assertEqual(row['state'],'draft')

    async def test_unknown_or_submitted_result_never_schedules_retry(self):
        for state in ('sending','submitted',''):
            with self.subTest(state=state):
                self.service.book.change(self.identity,'draft',payload={
                    'post':self.post,'comment':self.post['comments'][0]})
                row=await self.fail_once(UIJobError('Exact source unavailable',state))
                self.assertEqual(row['state'],'uncertain');self.assertNotIn('reply_retry',json.loads(row['payload']))
                self.assertIsNone(await self.service.resume_reply_retry(self.identity))
                self.service.book.change(self.identity,'cancelled')

    async def test_native_answer_arriving_during_backoff_cancels_retry(self):
        await self.fail_once();self.make_due()
        self.post['comments'].append({'id':'2','author':'account','own':True,'local':False,
            'text':'已手动回复','reply_to':'1','created':time.time()})
        self.assertIsNone(await self.service.resume_reply_retry(self.identity))
        self.assertEqual(self.service.book.get('account',self.identity)['state'],'skipped')

    async def test_changed_or_deleted_source_is_not_replied_to(self):
        await self.fail_once();self.make_due();self.post['comments'][0]['text']='已修改的另一句话'
        self.assertIsNone(await self.service.resume_reply_retry(self.identity))
        self.assertEqual(self.service.book.get('account',self.identity)['state'],'skipped')

    async def test_disabled_review_expiry_quota_and_busy_do_not_bypass_controls(self):
        await self.fail_once();self.make_due()
        for updates in ({'moments_reply_own_enabled':False},{'moments_mode':'review'},
                        {'moments_reply_own_per_day':0}):
            with self.subTest(updates=updates):
                self.runtime.config.settings=self.settings.model_copy(update=updates)
                self.assertIsNone(await self.service.resume_reply_retry(self.identity))
                self.assertEqual(self.service.book.get('account',self.identity)['state'],'failed')
        self.runtime.config.settings=self.settings
        self.runtime.processing='busy'
        self.assertIsNone(await self.service.resume_reply_retry(self.identity))
        self.runtime.processing='';self.make_due(expired=True)
        self.assertIsNone(await self.service.resume_reply_retry(self.identity))
        self.assertEqual(json.loads(self.service.book.get('account',self.identity)['payload'])['reply_retry']['status'],'expired')

    async def test_old_failed_records_without_retry_metadata_are_not_backfilled(self):
        self.service.book.change(self.identity,'failed',error='Exact source unavailable')
        self.assertIsNone(await self.service.resume_reply_retry(self.identity))
        self.assertFalse(self.calls)

    async def test_old_source_or_permissions_are_not_transient_retry(self):
        row=self.service.book.get('account',self.identity);p=json.loads(row['payload'])
        p['comment']['created']=time.time()-90000
        self.assertIsNone(self.service.reply_retry_payload(row,p,UIJobError('Exact source missing','failed_before_send')))
        p['comment']['created']=time.time()
        self.assertIsNone(self.service.reply_retry_payload(row,p,UIJobError('Moments account is not authorized','failed_before_send')))


if __name__=='__main__':unittest.main()
