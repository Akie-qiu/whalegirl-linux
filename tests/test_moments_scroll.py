"""Regressions for long native comments and nested navigation, without sending."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'deploy'))
import moments_ui
import moments_layout


class MomentsScrollTests(unittest.TestCase):
    def test_capture_replaces_old_png_with_current_dimensions(self):
        folder=ROOT/'test-runs';folder.mkdir(exist_ok=True)
        captures=Path(tempfile.mkdtemp(prefix='moments-capture-',dir=folder))
        Image.new('RGB',(548,111),'white').save(captures/'current.png')
        def capture(*args):
            self.assertNotEqual(Path(args[-1]),captures/'current.png')
            Image.new('RGB',(548,74),(230,230,230)).save(args[-1])
        with patch.object(moments_ui,'ROOT',captures),patch.object(moments_ui,'activate_moments'),\
             patch.object(moments_ui,'command',side_effect=capture):
            result=moments_ui.capture_area({'x':0,'y':0,'width':548,'height':74},'current')
        self.assertEqual(moments_layout.png_rows(result)[:2],(548,74))

    def test_absent_context_menu_never_sends_escape_to_moments(self):
        with patch.object(moments_ui,'tree',return_value={'role':'frame','name':'Moments'}),\
             patch.object(moments_ui,'command') as command:
            moments_ui.dismiss_comment_menu()
        command.assert_not_called()

    def test_timeline_comments_without_dividers_expose_text_lines(self):
        folder=ROOT/'test-runs';folder.mkdir(exist_ok=True)
        path=Path(tempfile.mkdtemp(prefix='moments-timeline-',dir=folder))/'comments.png'
        image=Image.new('RGB',(548,111),(247,247,247));draw=ImageDraw.Draw(image)
        for top in (12,44,73):draw.rectangle((20,top,180,top+10),fill=(80,90,120))
        image.save(path)
        self.assertEqual(moments_layout.timeline_comment_points(path),[17,49,78])

    def test_reply_uses_verified_timeline_comment_without_notification(self):
        target={'id':'1','author':'person','text':'source','created':1}
        post={'id':'post','comments':[target]}
        reply={'role':'menu-item','name':'Reply'}
        with patch.object(moments_ui,'select_timeline_comment',return_value=(500,300)) as select,\
             patch.object(moments_ui,'native_read',return_value={'posts':[post]}),\
             patch.object(moments_ui,'tree',return_value={'children':[reply]}),\
             patch.object(moments_ui,'command') as command,patch.object(moments_ui,'activate_moments'),\
             patch.object(moments_ui,'click_node') as click:
            self.assertEqual(moments_ui.reply_comment(post,{'comment':target}),{'native_comment':True})
        select.assert_called_once_with(post,target)
        click.assert_called_once_with(reply)
        self.assertEqual(command.call_count,1)

    def test_changed_comment_order_stops_before_reply(self):
        target={'id':'1','author':'person','text':'source','created':1}
        post={'id':'post','comments':[target]}
        with patch.object(moments_ui,'select_timeline_comment',return_value=(500,300)),\
             patch.object(moments_ui,'native_read',return_value={'posts':[{'id':'post','comments':[]}]}),\
             patch.object(moments_ui,'command') as command,patch.object(moments_ui,'activate_moments'),\
             patch.object(moments_ui,'click_node') as click:
            with self.assertRaisesRegex(ValueError,'Comment order changed'):
                moments_ui.reply_comment(post,{'comment':target})
        command.assert_not_called();click.assert_not_called()

    def test_nested_details_returns_through_messages_before_timeline(self):
        step=[0]
        def snapshot():
            name='Back' if step[0]<2 else 'Messages'
            return {'role':'frame','name':'Moments','bounds':{'x':0,'y':0,'width':550,'height':550},
                    'children':[{'role':'push-button','name':name,'bounds':{'x':1,'y':1,'width':40,'height':40}}]}
        def click(_):step[0]+=1
        with patch.object(moments_ui,'tree',side_effect=snapshot),patch.object(moments_ui,'click_node',side_effect=click),patch.object(moments_ui,'command',return_value=SimpleNamespace(stdout=b'123\n')),patch.object(moments_ui.time,'sleep'):
            result=moments_ui.open_moments()
        self.assertEqual(step[0],2)
        self.assertEqual(result['children'][0]['name'],'Messages')

    def test_verified_comment_block_survives_post_accessible_scrolling_offscreen(self):
        f={'role':'frame','name':'Moments','children':[{'role':'list','bounds':{'x':0,'y':40,'width':548,'height':477},
            'children':[{'role':'list-item','name':'','bounds':{'x':0,'y':-25,'width':548,'height':542}}]}]}
        with patch.object(moments_ui,'frame',return_value=f):
            bounds,area=moments_ui.comment_detail({'text':'source post'},542)
            self.assertEqual(bounds['height'],542)
            with self.assertRaises(ValueError):moments_ui.comment_detail({'text':'source post'},600)

    def test_viewport_segments_allow_clipped_rows_and_skip_likes_strip(self):
        folder=ROOT/'test-runs';folder.mkdir(exist_ok=True)
        path=Path(tempfile.mkdtemp(prefix='moments-scroll-',dir=folder))/'viewport.png'
        image=Image.new('RGB',(548,342),'white');draw=ImageDraw.Draw(image)
        for y in (48,110,169,225,296):draw.line((160,y,507,y),fill=(235,235,235))
        image.save(path)
        points=moments_layout.visible_comment_points(path,starts_at_top=True)
        self.assertEqual(len(points),5)
        self.assertTrue(all(48<p<342 for p in points))
        self.assertNotIn(33,points)

