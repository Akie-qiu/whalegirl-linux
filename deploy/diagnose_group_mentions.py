"""Inspect the @ picker in the authorized test group without sending a message."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, '/data/native-tools')
import chat_select_linux as ui
import native_group_mentions as native
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
    roster=ui.native_read('/members',chat=chat['id']).get('members',[])
    alias=None
    aliases={}
    for member in roster:
        for value in member.get('aliases',[])+[member.get('name','')]:
            if value:aliases.setdefault(ui.normal(value),[]).append(member)
    for member in roster:
        for value in member.get('aliases',[])+[member.get('name','')]:
            if value and len(aliases.get(ui.normal(value),[]))==1:
                alias=value;break
        if alias:break
    if not alias:raise RuntimeError('Current group has no uniquely searchable member')
    try:
        ui.click_node(editors[0])
        ui.run_process(['xdotool','key','--clearmodifiers','at'],check=True,timeout=3)
        ui.run_process(['xclip','-selection','clipboard'],input=alias.encode(),stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,check=True,timeout=3)
        ui.run_process(['xdotool','key','--clearmodifiers','ctrl+v'],check=True,timeout=3)
        end=time.monotonic()+2
        selected_target=None
        observed=[]
        while time.monotonic()<end:
            current=ui.tree()
            rows=native.picker_rows(current,editors[0])
            observed=[len(n.get('name','')) for n in rows]
            matches=[n for n in rows if ui.normal(n.get('name'))==ui.normal(alias)]
            if len(matches)==1:
                selected_target=matches[0];break
            time.sleep(.06)
        if selected_target is None:raise RuntimeError('Native member picker did not expose the unique roster target; observed_lengths='+','.join(map(str,observed)))
        ui.click_node(selected_target)
        time.sleep(.2)
        current=ui.tree()
        still_listed=any(ui.normal(n.get('name'))==ui.normal(alias)
                         for n in native.picker_rows(current,editors[0]))
        print(json.dumps({'roster_members':len(roster),'unique_target_present':True,
            'selected_from_native_picker':not still_listed,
            'composer_unique':len([n for n in ui.walk(current) if n.get('role')=='text' and
                'EDITABLE' in n.get('states',[]) and n.get('bounds')==editors[0]['bounds']])==1}))
    finally:
        ui.run_process(['xdotool','key','--clearmodifiers','Escape','BackSpace'],check=True,timeout=3)


if __name__ == '__main__':main()
