"""Inspect native controls without submitting posts, comments, or stickers."""
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, '/data/native-tools')
import chat_select_linux as ui
import moments_ui as moments


def controls(root):
    allowed = {'Moments', 'Post', 'Post Text', 'Cancel', 'Messages', 'Back',
               'Custom Stickers', 'Close', 'Refresh'}
    return [{k:n.get(k) for k in ('role', 'name', 'bounds')}
            for n in ui.walk(root) if n.get('name') in allowed]


def main():
    pid=subprocess.check_output(['pgrep','-x','wechat'],text=True).split()[0]
    env=dict(v.split('=',1) for v in Path('/proc/'+pid+'/environ').read_text().split('\0') if '=' in v)
    os.environ['DISPLAY']=':99'
    if env.get('DBUS_SESSION_BUS_ADDRESS'):os.environ['DBUS_SESSION_BUS_ADDRESS']=env['DBUS_SESSION_BUS_ADDRESS']
    result={}
    ui.SKIP_MESSAGES=True
    original=moments.wait_for
    def traced(find, seconds=2, **kwargs):
        try:return original(find,seconds,**kwargs)
        except Exception:
            result['failed_wait']=controls(ui.tree())
            raise
    moments.wait_for=traced
    try:
        moments.open_moments()
        result['timeline']=controls(ui.tree())
        try:
            container=moments.prepare_publish({})
            result['composer']=controls(container)
            result['editable_count']=len(moments.editors(container))
            ui.click_node(moments.button(container,'Cancel'))
        except Exception as exc:result['publish_error']=str(exc)
    except Exception as exc:result['open_error']=str(exc)
    finally:moments.close_moments()
    try:
        root=ui.tree()
        buttons=[n for n in ui.walk(root) if n.get('role')=='push-button' and n.get('name')=='Send sticker(Alt+E)']
        if len(buttons)==1:ui.click_node(buttons[0])
        root=ui.tree()
        tabs=[n for n in ui.walk(root) if n.get('role')=='page-tab' and n.get('name')=='Custom Stickers']
        if len(tabs)==1:ui.click_node(tabs[0]);root=ui.tree()
        result['favorite_count']=len(ui.native_read('/stickers')['stickers'])
        result['sticker_controls']=controls(root)
        result['sticker_lists']=[{'name':n.get('name'),'bounds':n.get('bounds'),'children':len(n.get('children',[]))}
            for n in ui.walk(root) if n.get('role')=='list' and any(c.get('name')=='Custom Stickers' for c in n.get('children',[]))]
        area=next(n['bounds'] for n in ui.walk(root) if n.get('role')=='list' and any(c.get('name')=='Custom Stickers' for c in n.get('children',[])))
        result['pages']=[]
        from a11y_safe import A11yBus,dump_tree
        class InspectBus(A11yBus):
            def __init__(self):super().__init__();self.positions={};self.favorites=[]
            def children(self,ref):
                refs=super().children(ref)
                for i,child in enumerate(refs):self.positions[child]=(i,len(refs))
                return refs
            def properties(self,ref):
                value=super().properties(ref)
                if value.get('Name')=='Custom Stickers' and self.role(ref)=='label':
                    self.favorites.append({'index':self.positions.get(ref),'bounds':self.bounds(ref),'ref':ref})
                return value
        for direction,count in [('4',40),('5',1),('5',1),('5',3),('5',40)]:
            moments.command('xdotool','mousemove',str(round(area['x']+area['width']/2)),str(round(area['y']+area['height']/2)),
                'click','--repeat',str(count),'--delay','5',direction)
            moments.time.sleep(.12)
            bus=InspectBus();dump_tree(bus,skip_messages=True)
            result['pages'].append({'scroll':direction,'steps':count,'tiles':bus.favorites})
        moments.command('xdotool','key','--clearmodifiers','Escape')
    except Exception as exc:result['sticker_error']=str(exc)
    print(json.dumps(result))


if __name__=='__main__':main()
