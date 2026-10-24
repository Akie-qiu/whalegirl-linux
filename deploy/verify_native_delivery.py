"""Exercise the live controls with the final send click intercepted."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0,'/data/native-tools')
import chat_select_linux as ui
import moments_ui as moments


def main():
    pid=subprocess.check_output(['pgrep','-x','wechat'],text=True).split()[0]
    env=dict(v.split('=',1) for v in Path('/proc/'+pid+'/environ').read_text().split('\0') if '=' in v)
    os.environ['DISPLAY']=':99'
    if env.get('DBUS_SESSION_BUS_ADDRESS'):os.environ['DBUS_SESSION_BUS_ADDRESS']=env['DBUS_SESSION_BUS_ADDRESS']
    ui.SKIP_MESSAGES=True
    result={'submitted_messages':0}
    root=ui.tree()
    choices=[c for c in ui.chats() if ui.selected(root,ui.normal(c.get('remark') or c.get('name')),bool(c.get('isGroup')))]
    if len(choices)!=1:raise RuntimeError('Cannot identify current chat for non-sending control test')
    chat=choices[0];name=ui.normal(chat.get('remark') or chat['name'])
    order=[r['md5'] for r in ui.native_read('/stickers')['stickers']]
    folder=Path('/data/whalegirl-jobs/diagnostics/native-delivery-'+str(time.time_ns()))
    folder.mkdir(parents=True,mode=0o700)
    result['sticker_pages']=[]
    original=ui.run_process
    for index in sorted(set([0,min(25,len(order)-1),min(30,len(order)-1),len(order)-1])):
        path=folder/('sticker-'+str(index)+'.json')
        job={'md5':order[index],'diagnostic_only':True}
        path.write_text(json.dumps(job));clicks=[]
        def intercept(args,**kwargs):
            if args[0]=='/opt/tools/click' and json.loads(path.read_text()).get('state')=='sending':
                clicks.append(args[1:]);return subprocess.CompletedProcess(args,0)
            return original(args,**kwargs)
        ui.run_process=intercept
        started=time.monotonic()
        try:
            ui.send_native_sticker(job,path,ui.tree(),name,bool(chat.get('isGroup')))
            if len(clicks)!=1:raise RuntimeError('Expected one intercepted final sticker click')
            result['sticker_pages'].append({'index':index,'ok':True,'seconds':round(time.monotonic()-started,2)})
        except Exception as exc:
            raise RuntimeError('index='+str(index)+' error='+str(exc)+' scroll='+json.dumps(job.get('favorite_scroll',[]))) from None
        finally:ui.run_process=original
    # Reproduce a leftover sticker overlay immediately before opening Moments.
    root=ui.tree()
    button=next(n for n in ui.walk(root) if n.get('role')=='push-button' and n.get('name')=='Send sticker(Alt+E)')
    ui.click_node(button)
    try:
        moments.open_moments()
        composer=moments.prepare_publish({})
        editors=moments.editors(composer)
        if len(editors)!=1:raise RuntimeError('Publish editor is not unique')
        moments.put_text(editors[0],'界面检查草稿，不会发布')
        moments.button(moments.composer(ui.tree()),'Post')
        # Native Cancel retains a nonempty draft. Clear only the synthetic
        # text just entered by this verifier before dismissing the composer.
        ui.click_node(moments.editors(moments.composer(ui.tree()))[0])
        moments.command('xdotool','key','--clearmodifiers','ctrl+a','BackSpace')
        ui.click_node(moments.button(moments.composer(ui.tree()),'Cancel'))
        moments.wait_for(lambda r: not moments.editors(moments.frame(r)) or (_ for _ in ()).throw(ValueError('Verifier editor still open')),
                         stage='cancel synthetic draft')
        result['publish_with_sticker_overlay']='editor_ready_and_cancelled'
    finally:moments.close_moments()
    print(json.dumps(result))


if __name__=='__main__':main()
