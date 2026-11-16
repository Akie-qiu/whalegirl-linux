"""Bounded synthetic source material only; no Bilibili access or real messages."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

from PIL import Image
from whalegirl_linux.bilibili_client import BiliError,ReadBudget
from whalegirl_linux.bilibili_client import BilibiliClient
from whalegirl_linux.bilibili_reader import VideoReader
from whalegirl_linux.growth import GrowthService
from whalegirl_linux.settings import Settings
from whalegirl_linux.video_media import clear_workspace,extract_frames

BV='BV1234567890'
ROOT=Path(__file__).resolve().parents[1]


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_new_snapshot_cdn_binary_index_and_https_upgrade(self):
        image=Image.new('RGB',(40,20),'red');image.paste('blue',(20,0,40,20))
        out=io.BytesIO();image.save(out,'JPEG')
        class Client:
            async def api(self,*args,**kwargs):
                return {'pvdata':'//boss.hdslb.com/videoshotpvhdboss/fixture.bin','image':['http://bimp.hdslb.com/videoshotpvhdboss/fixture-0001.jpg'],
                    'img_x_len':2,'img_y_len':1,'img_x_size':20,'img_y_size':20}
            async def get(inner,url,*,kind,**kwargs):
                self.assertTrue(BilibiliClient.permitted(url,kind));self.assertTrue(url.startswith('https://'))
                return b'\x00\x00\x00\x28' if kind=='frame_index' else out.getvalue()
        frames,meta=await VideoReader(Client()).frames(BV,1,55,ReadBudget())
        self.assertEqual([row['time'] for row in frames],[0,40])
        self.assertFalse(BilibiliClient.permitted('https://bimp.hdslb.com/other/fixture.jpg','frame'))
        self.assertFalse(BilibiliClient.permitted('https://bimp.hdslb.com.evil.example/videoshotpvhdboss/a.jpg','frame'))
        self.assertFalse(BilibiliClient.permitted('https://boss.hdslb.com/videoshotpvhdboss/a.json','frame_index'))

    async def test_comment_pages_nested_context_dedup_and_hard_ceiling(self):
        settings=Settings(bilibili_comments_limit=80,bilibili_comment_pages=5,bilibili_reply_threads=3)
        calls=[]
        class Client:
            async def api(self,path,params,**kwargs):
                calls.append((path,params))
                if path.endswith('/reply'):
                    return {'replies':[{'rpid':1000+int(params['root'])*10+i,'content':{'message':'楼中楼回应第'+str(i)+'，接着讨论'+params['root']}} for i in range(10)]}
                page=int(params['next'])
                return {'replies':[{'rpid':page*25+i+1,'content':{'message':'真实场景中的不同想法 '+str(page*25+i)}} for i in range(25)]+[
                    {'rpid':9000+page,'content':{'message':'加微信群领取日赚返现'}},
                    {'rpid':8000+page,'content':{'message':'真实场景中的不同想法 0'}}],
                    'cursor':{'next':page+1,'is_end':False}}
        rows,coverage=await VideoReader(Client()).comments(1,BV,ReadBudget(),settings,[])
        self.assertEqual(len(rows),80);self.assertGreaterEqual(coverage['pages_read'],3)
        self.assertLessEqual(coverage['pages_read'],5);self.assertLessEqual(coverage['reply_threads_read'],3)
        self.assertTrue(coverage['target_met']);self.assertFalse(coverage['complete_comments'])
        self.assertTrue(any(row['parent_id'] for row in rows))
        roots={row['id'] for row in rows if not row['parent_id']}
        self.assertTrue(all(not row['parent_id'] or row['parent_id'] in roots for row in rows))
        self.assertEqual(len({row['text'] for row in rows}),80)
        self.assertTrue(all('返现' not in row['text'] for row in rows))

    async def test_minimum_comment_limit_reserves_slots_across_multiple_reply_threads(self):
        settings=Settings(bilibili_comments_limit=20,bilibili_comment_pages=2,bilibili_reply_threads=3)
        calls=[]
        class Client:
            async def api(self,path,params,**kwargs):
                calls.append((path,params))
                if path.endswith('/reply'):
                    return {'replies':[{'rpid':1000+int(params['root'])*10+i,
                        'content':{'message':'不同楼层的自然回复 '+str(i)+' '+str(params['root'])}} for i in range(10)]}
                return {'replies':[{'rpid':i+1,'content':{'message':'父楼提出不同问题 '+str(i)}} for i in range(30)],
                    'cursor':{'next':1,'is_end':False}}
        rows,coverage=await VideoReader(Client()).comments(1,BV,ReadBudget(),settings,[])
        reply_calls=[params for path,params in calls if path.endswith('/reply')]
        self.assertEqual(len(rows),20)
        self.assertEqual(len(reply_calls),3)
        self.assertEqual([params['ps'] for params in reply_calls],[4,4,4])
        roots={row['id'] for row in rows if not row['parent_id']}
        self.assertEqual(len(roots),8)
        self.assertTrue(all(row['parent_id'] in roots for row in rows if row['parent_id']))
        self.assertEqual({row['parent_id'] for row in rows if row['parent_id']},
            {str(params['root']) for params in reply_calls})
        self.assertEqual(coverage['reply_threads_read'],3)

    async def test_nested_reply_keeps_its_actual_parent_identity(self):
        settings=Settings(bilibili_comments_limit=20,bilibili_comment_pages=1,bilibili_reply_threads=1)
        class Client:
            async def api(self,path,params,**kwargs):
                if path.endswith('/reply'):
                    return {'replies':[{'rpid':21,'parent':11,'content':{'message':'第一层回应'}},
                        {'rpid':22,'parent':21,'content':{'message':'接着第一层回应'}}]}
                return {'replies':[{'rpid':11,'content':{'message':'父楼提出讨论'}}],
                    'cursor':{'next':1,'is_end':False}}
        rows,_=await VideoReader(Client()).comments(1,BV,ReadBudget(),settings,[])
        by_id={row['id']:row for row in rows}
        self.assertEqual(by_id['21']['parent_id'],'11')
        self.assertEqual(by_id['22']['parent_id'],'21')
        self.assertEqual(by_id['22']['root_id'],'11')

    async def test_empty_comments_never_fabricate_target_and_risk_has_no_fallback(self):
        client=type('Client',(),{})();client.api=AsyncMock(return_value={'replies':[],'cursor':{'is_end':True}})
        reader=VideoReader(client);rows,coverage=await reader.comments(1,BV,ReadBudget(),Settings(),[])
        self.assertEqual(rows,[]);self.assertFalse(coverage['target_met']);self.assertTrue(coverage['end_confirmed'])
        client.api=AsyncMock(side_effect=BiliError('risk_paused','访问限制'))
        with self.assertRaises(BiliError):await reader.comments(1,BV,ReadBudget(),Settings(),[])
        self.assertEqual(client.api.await_count,1)

    async def test_danmaku_samples_across_window_without_claiming_consensus(self):
        raw=('<i>'+''.join('<d p="'+str(i)+',1,25">这段反转真没想到 '+str(i)+'</d>' for i in range(300))+
            '<d p="20,1,25">加微信群领取返现</d><d p="301,1,25">窗口以外</d></i>').encode()
        client=type('Client',(),{})();client.get=AsyncMock(return_value=raw)
        rows,meta=await VideoReader(client).danmaku(1,400,0,300,ReadBudget(),Settings(),[])
        self.assertEqual(len(rows),120);self.assertGreater(rows[-1]['time'],270);self.assertLessEqual(rows[0]['time'],5)
        self.assertFalse(meta['complete']);self.assertEqual(meta['distinct_in_window'],300)
        self.assertTrue(all('返现' not in row['text'] for row in rows))

    async def test_multipart_window_preserves_timestamps_and_real_gaps(self):
        class Client:
            async def api(self,path,params,**kwargs):
                return {'bvid':BV,'aid':1,'cid':22,'subtitle':{'subtitles':[{'lan':'zh-CN','subtitle_url':'https://aisubtitle.hdslb.com/bfs/fixture.json'}]}}
            async def get(self,*args,**kwargs):
                return {'body':[{'from':290,'to':310,'content':'上一窗与这一窗交界处'},
                    {'from':350,'to':370,'content':'新的第二段字幕'},{'from':620,'to':640,'content':'本窗之外'}]},None
        meta={'bvid':BV,'aid':1,'title':'合成多分P','pages':[{'page':1,'cid':11,'duration':20},
            {'page':2,'cid':22,'duration':900,'part':'第二部分'}]}
        settings=Settings(bilibili_visual_enabled=False,bilibili_comments_enabled=False,bilibili_danmaku_enabled=False)
        material=await VideoReader(Client()).read(BV,ReadBudget(),settings,metadata=meta,page=2,start=300)
        coverage=material['coverage']
        self.assertEqual(material['cid'],22);self.assertEqual(material['page'],2)
        self.assertEqual(coverage['segment_end'],600);self.assertEqual(material['segments'][0]['start'],290)
        self.assertEqual(coverage['window_without_subtitles'],[[310,350],[370,600]])
        self.assertEqual(coverage['unread_time_ranges'],[[0,300],[310,350],[370,600],[600,900]])
        self.assertFalse(coverage['complete_playback'])
        with self.assertRaises(BiliError):await VideoReader(Client()).read(BV,ReadBudget(),settings,metadata=meta,page=3)

    async def test_incomplete_local_decode_uses_official_snapshot_fallback(self):
        class Client:
            async def api(self,*args,**kwargs):return {'subtitle':{'subtitles':[]}}
        reader=VideoReader(Client())
        def frame(stamp):
            out=io.BytesIO();Image.new('RGB',(40,20),'blue').save(out,'JPEG')
            return {'time':stamp,'jpeg':out.getvalue()}
        reader.frames=AsyncMock(return_value=([frame(0),frame(40)],{'media_mode':'official_snapshot','scan_end':40}))
        metadata={'bvid':BV,'aid':1,'pages':[{'page':1,'cid':2,'duration':55}]}
        settings=Settings(bilibili_comments_enabled=False,bilibili_danmaku_enabled=False)
        with patch('whalegirl_linux.bilibili_reader.media_frames',new=AsyncMock(return_value=(
                [frame(0),frame(1)],{'media_mode':'ffmpeg_local','decoder_completed':False,'decoded_sample_end':1}))):
            material=await reader.read(BV,ReadBudget(),settings,metadata=metadata,config=object())
        self.assertEqual(material['frames'][-1]['time'],40);self.assertEqual(reader.frames.await_count,1)
        coverage=material['coverage']
        self.assertFalse(coverage['frame_selection']['ffmpeg_attempt']['decoder_completed'])
        self.assertEqual(coverage['unread_time_ranges'],[[0,55]])

    async def test_reflection_material_keeps_all_sections_and_valid_json_within_context(self):
        body={'subtitles':[{'start':i,'end':i+2,'text':'字幕原文'*100} for i in range(1500)],
            'scenes':[{'number':i,'time':i,'visible':'真实可见画面'*100} for i in range(24)],
            'comments':[{'id':str(i),'text':'不同意见'*100} for i in range(80)],
            'danmaku':[{'time':i,'text':'网友反应'*100} for i in range(120)]}
        output=GrowthService.source_excerpt({'kind':'bilibili','body':json.dumps(body,ensure_ascii=False)})
        parsed=json.loads(output);self.assertLessEqual(len(output),18000)
        self.assertTrue(all(parsed[key] for key in body))
        self.assertGreater(parsed['subtitles'][-1]['start'],1000)

    async def test_ffmpeg_is_local_serial_bounded_and_own_cleanup_does_not_follow_links(self):
        root=ROOT/'test-runs';root.mkdir(exist_ok=True)
        folder=Path(tempfile.mkdtemp(prefix='media-',dir=root));work=folder/('a'*32);work.mkdir()
        target=work/'media.mp4';target.write_bytes(b'fixture')
        image=io.BytesIO();Image.new('RGB',(40,20),'blue').save(image,'JPEG')
        commands=[]
        async def process(args,timeout):
            commands.append((args,timeout))
            if 'ffprobe' in args[0]:return 0,b'{"streams":[{"width":320,"height":180}]}'
            (work/'frame-000000.jpg').write_bytes(image.getvalue())
            return 0,b''
        with patch('whalegirl_linux.video_media.run_process',side_effect=process):
            frames,meta=await extract_frames(target,0,300,ffmpeg='ffmpeg',ffprobe='ffprobe')
        self.assertEqual(len(frames),1);self.assertEqual(meta['decoder_threads'],1)
        self.assertIn('file,pipe',commands[-1][0]);self.assertIn('120',commands[-1][0])
        self.assertTrue(all(not any('http' in part for part in command) for command,_ in commands))
        unknown=work/'keep-unknown.txt';unknown.write_text('keep')
        clear_workspace(folder,work)
        self.assertTrue(unknown.exists());self.assertFalse(target.exists())
        self.assertFalse((work/'frame-000000.jpg').exists())


if __name__=='__main__':unittest.main()
