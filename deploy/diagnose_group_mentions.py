"""Inspect the @ picker in the authorized test group without sending a message."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, '/data/native-tools')
import chat_select_linux as ui
from a11y_safe import A11yBus, DESKTOP


def main():
    pid = subprocess.check_output(['pgrep','-x','wechat'],text=True).split()[0]
    env = dict(v.split('=',1) for v in Path('/proc/'+pid+'/environ').read_text().split('\0') if '=' in v)
    os.environ['DISPLAY'] = ':99'
    if env.get('DBUS_SESSION_BUS_ADDRESS'):os.environ['DBUS_SESSION_BUS_ADDRESS'] = env['DBUS_SESSION_BUS_ADDRESS']
    ui.SKIP_MESSAGES = True
    chats = ui.chats()
    root = ui.tree()
    selected = [c for c in chats if c.get('isGroup') and
                ui.selected(root,ui.normal(c.get('remark') or c.get('name')),True)]
    if len(selected)==1:
        chat=selected[0]
    else:
        chat = next(c for c in chats if (c.get('remark') or c.get('name')) == '临时测试')
        chosen = subprocess.run(['python3','/opt/tools/chat-select.py',chat['id']],capture_output=True,text=True,timeout=18)
        if chosen.returncode:
            raise RuntimeError(json.loads(chosen.stdout).get('error','Cannot open test group'))
    root = ui.tree()
    editors = [n for n in ui.walk(root) if n.get('role')=='text' and 'EDITABLE' in n.get('states',[]) and
               n.get('bounds',{}).get('height',0)>35 and n.get('bounds',{}).get('y',0)>200]
    if len(editors)!=1:raise RuntimeError('Composer not unique')
    bounds = editors[0]['bounds']
    bus = A11yBus();seen=set();values=[]
    def visit(ref):
        if ref in seen:return
        seen.add(ref)
        try:
            role = bus.role(ref)
            if role=='list' and bus.properties(ref).get('Name') in ['Messages','Chats']:return
            if role=='text' and bus.bounds(ref)==bounds:
                values.append(bus.call(ref,'org.a11y.atspi.Text','GetText','(ii)',(0,-1)));return
            for child in bus.children(ref):visit(child)
        except Exception:pass
    visit(DESKTOP)
    if values!=['']:raise RuntimeError('Existing draft preserved; no typing')
    def identity(n):return (n.get('role'),n.get('name'),json.dumps(n.get('bounds'),sort_keys=True))
    previous = {identity(n) for n in ui.walk(root)}
    try:
        ui.click_node(editors[0])
        ui.run_process(['xdotool','key','--clearmodifiers','at'],check=True,timeout=3)
        time.sleep(.2)
        nodes = [n for n in ui.walk(ui.tree()) if identity(n) not in previous]
        print(json.dumps({'new_controls':[{'role':n.get('role'),'name_length':len(n.get('name','')),
            'is_target':n.get('name')=='飘·渺','bounds':n.get('bounds'),'states':n.get('states'),
            'child_roles':[c.get('role') for c in n.get('children',[])]} for n in nodes][:80]}))
    finally:
        ui.run_process(['xdotool','key','--clearmodifiers','Escape','BackSpace'],check=True,timeout=3)


if __name__ == '__main__':main()
