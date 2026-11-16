"""Narrow native UI compatibility for the owner-authorized 4.1.13 client."""
import json
from pathlib import Path
import re
import subprocess
import time


def modern_client():
    path = Path('/data/runtime-version.json')
    return path.is_file() and json.loads(path.read_text()).get('wechat_version') == '4.1.13.26'


def restore_main(ui):
    root = ui.tree()
    if any(n.get('role') == 'frame' and n.get('name') in ('Weixin', '微信') and
           (n.get('bounds') or {}).get('width', 0) >= 800 for n in ui.walk(root)):
        return
    pid = ui.run_process(['pgrep', '-xo', 'wechat'], capture_output=True, text=True, check=True, timeout=3).stdout.strip()
    windows = ui.run_process(['xdotool', 'search', '--pid', pid, '--name', '^Weixin$'],
                             capture_output=True, text=True, check=True, timeout=3).stdout.split()
    trays = []
    for window in windows:
        geometry = ui.run_process(['xdotool', 'getwindowgeometry', '--shell', window],
                                  capture_output=True, text=True, check=True, timeout=3).stdout
        b = dict(re.findall(r'^(X|Y|WIDTH|HEIGHT)=(-?\d+)$', geometry, re.M))
        if len(b) == 4 and 10 <= int(b['WIDTH']) <= 40 and 10 <= int(b['HEIGHT']) <= 40 and int(b['Y']) > 700:
            trays.append(b)
    if len(trays) != 1:
        raise ValueError('无法唯一定位当前微信托盘入口，未点击或重启')
    b = trays[0]
    ui.run_process(['xdotool', 'mousemove', str(int(b['X']) + int(b['WIDTH'])//2),
                    str(int(b['Y']) + int(b['HEIGHT'])//2), 'click', '1'], check=True, timeout=3)
    time.sleep(.3)


def group_search_result(root, name, walk):
    matches = []
    for section in walk(root):
        if section.get('role') != 'list' or section.get('name'):
            continue
        in_groups = False
        for row in section.get('children', []):
            label = row.get('name', '')
            height = (row.get('bounds') or {}).get('height', 0)
            if label in ('Group Chats', '群聊'):
                in_groups = True
                continue
            if in_groups and height <= 36:
                in_groups = False
            if in_groups and row.get('role') == 'list-item' and label == name and height > 40:
                matches.append(row)
    if len(matches) != 1:
        raise ValueError('搜索结果中无法唯一确认群聊；未使用聊天记录或搜索建议作为会话')
    return matches[0]


def open_group_by_search(ui, name):
    root = ui.tree()
    chats = [n for n in ui.walk(root) if n.get('role') == 'list' and n.get('name') == 'Chats']
    if len(chats) != 1:
        raise ValueError('群聊搜索前无法确认聊天列表')
    top = chats[0]['bounds']['y']
    fields = [n for n in ui.walk(root) if n.get('role') == 'text' and n.get('name') in ('Search', '搜索')
              and 'EDITABLE' in n.get('states', []) and (n.get('bounds') or {}).get('y', 10000) < top]
    if len(fields) != 1:
        raise ValueError('无法唯一确认会话搜索框，未在消息输入区输入')
    ui.click_node(fields[0])
    ui.run_process(['xdotool', 'key', '--clearmodifiers', 'ctrl+a'], check=True, timeout=3)
    ui.run_process(['xclip', '-selection', 'clipboard'], input=name.encode(), stdout=subprocess.DEVNULL, check=True, timeout=3)
    ui.run_process(['xdotool', 'key', '--clearmodifiers', 'ctrl+v'], check=True, timeout=3)
    deadline = time.monotonic() + 5
    result = None
    while time.monotonic() < deadline:
        try:
            result = group_search_result(ui.tree(), name, ui.walk)
            break
        except ValueError:
            time.sleep(.15)
    if result is None:
        raise ValueError('未找到唯一群聊搜索结果，未发送')
    ui.click_node(result)
    time.sleep(.15)
    root = ui.tree()
    # The search popup remains above the chat list on this client. Clear only
    # the confirmed search entry, then focus (without editing) the composer.
    fields = [n for n in ui.walk(root) if n.get('role') == 'text' and n.get('name') in ('Search', '搜索')
              and 'EDITABLE' in n.get('states', [])]
    if len(fields) != 1:
        raise ValueError('搜索后无法确认原搜索框，未操作消息正文')
    ui.click_node(fields[0])
    ui.run_process(['xdotool', 'key', '--clearmodifiers', 'ctrl+a', 'BackSpace'], check=True, timeout=3)
    root = ui.tree()
    editors = [n for n in ui.walk(root) if n.get('role') == 'text' and 'EDITABLE' in n.get('states', [])
               and (n.get('bounds') or {}).get('y', 0) > top + 150]
    if len(editors) != 1:
        raise ValueError('无法唯一定位已打开群聊的编辑区，未发送')
    ui.click_node(editors[0])
    ui.locate_chat(ui.tree(), name)
    for _ in range(12):
        root = ui.tree()
        if ui.selected(root, name, True):
            return
        time.sleep(.15)
    raise ValueError('群聊搜索后未同时确认选中会话和标题，未发送')
