"""Insert native mention chips via the pinned client's member picker."""
import json
from pathlib import Path
import time

import chat_select_linux as ui

ROOT = Path('/data/whalegirl-jobs')


def mention_allowed(chat):
    authority = json.loads((ROOT/'authorized-chats.json').read_text())
    return (authority.get('group_mentions_enabled', True) is True and chat.endswith('@chatroom') and
            chat in authority.get('allowed_chat_ids',[]))


def picker_rows(root, editor):
    area = editor['bounds']
    rows = []
    for node in ui.walk(root):
        b = node.get('bounds') or {}
        if (node.get('role')=='list' and not node.get('name') and 0<b.get('width',0)<480 and
                0<b.get('height',0)<420 and abs(b.get('x',0)-area['x'])<400 and
                area['y']-420<b.get('y',0)<area['y']+area['height']):
            rows.extend(n for n in node.get('children',[]) if n.get('role')=='list-item')
    return rows


def unique_member_alias(member, roster):
    aliases = member.get('aliases',[]) + [member.get('name','')]
    for alias in dict.fromkeys(aliases):
        if (alias and len(alias)<=100 and alias not in ('所有人','所有成员','All','All members') and
                sum(alias in set(m.get('aliases',[])+[m.get('name','')]) for m in roster)==1):
            return alias
    raise ValueError('群成员名称重复，无法准确选择@目标')


def insert_mentions(job, editor):
    if not mention_allowed(job['chat']):
        job['mention_fallback']='群聊@成员总开关已关闭'
        return []
    snapshot = ui.native_read('/members',chat=job['chat'])
    roster = snapshot.get('members',[])
    by_id = {m['id']:m for m in roster}
    targets = list(dict.fromkeys(m.get('id') for m in job.get('mentions',[]) if isinstance(m,dict)))[:3]
    if not targets or any(identity not in by_id for identity in targets):
        raise ValueError('@目标不是当前群成员')
    labels = []
    for identity in targets:
        member = by_id[identity]
        alias = unique_member_alias(member,roster)
        ui.run_process(['xdotool','key','--clearmodifiers','at'],check=True,timeout=3)
        ui.run_process(['xclip','-selection','clipboard'],input=alias.encode(),
                       stdout=ui.subprocess.DEVNULL,stderr=ui.subprocess.DEVNULL,check=True,timeout=3)
        ui.run_process(['xdotool','key','--clearmodifiers','ctrl+v'],check=True,timeout=3)
        end = time.monotonic()+1.6
        while True:
            root = ui.tree()
            matches = [n for n in picker_rows(root,editor) if ui.normal(n.get('name')) in
                       {ui.normal(v) for v in member.get('aliases',[])+[member['name']]}]
            if len(matches)==1:
                ui.click_node(matches[0])
                labels.append(matches[0]['name'])
                break
            if time.monotonic()>=end:
                raise ValueError('微信@成员选择框未找到唯一目标')
            time.sleep(.06)
    job['mention_labels'] = labels
    return targets


def clear_generated_mentions():
    ui.run_process(['xdotool','key','--clearmodifiers','Escape','ctrl+a','BackSpace'],check=True,timeout=3)
