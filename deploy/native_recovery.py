"""Return native WeChat to its chat surface inside the gateway GUI guard."""
import json
from pathlib import Path
import time

ROOT = Path('/data/whalegirl-jobs')


def chat_navigation(ui, root):
    buttons = [n for n in ui.walk(root) if n.get('role') == 'push-button']
    exact = [n for n in buttons if n.get('name') in ('Chats', 'Chat', '聊天')]
    if len(exact) == 1:
        return exact[0]
    # 4.1.1.8 exposes the chat icon without a name, directly above Contacts.
    contacts = [n for n in buttons if n.get('name') in ('Contacts', '通讯录')]
    if len(contacts) != 1:
        return None
    anchor = contacts[0].get('bounds') or {}
    candidates = []
    for node in buttons:
        bounds = node.get('bounds') or {}
        if (bounds.get('width', 0) > 0 and bounds.get('height', 0) > 0
                and abs(bounds.get('x', -1000) - anchor.get('x', 0)) <= 4
                and 0 < anchor.get('y', 0) - bounds.get('y', 0) <= 70):
            candidates.append(node)
    return candidates[0] if len(candidates) == 1 else None


def reset_surface(ui):
    root = ui.tree()
    moments = [n for n in ui.walk(root) if n.get('role') == 'frame' and n.get('name') == 'Moments']
    if moments:
        from moments_ui import editors, button
        if len(moments) != 1 or editors(moments[0]):
            raise ValueError('朋友圈编辑器仍打开，保留草稿，请先完成或关闭编辑')
        ui.click_node(button(moments[0], 'Close'))
    ui.close_previews(ui.preview_windows())
    # No typing, Return, clipboard edits, process restart or login operation.
    for _ in range(2):
        ui.run_process(['xdotool', 'key', '--clearmodifiers', 'Escape'], check=True, timeout=3)
    root = ui.tree()
    navigation = chat_navigation(ui, root)
    if navigation:
        ui.click_node(navigation)
        time.sleep(.12)
        root = ui.tree()
    lists = [n for n in ui.walk(root) if n.get('role') == 'list' and n.get('name') == 'Chats']
    if len(lists) != 1 or not (lists[0].get('bounds') or {}).get('width'):
        raise ValueError('未能确认微信聊天主界面')
    return {'main_ready': True}


def run(path, target_id):
    import chat_select_linux as ui
    job = json.loads(path.read_text())
    authority = json.loads((ROOT / 'authorized-chats.json').read_text())
    if (job.get('operation') != 'reset_ui' or job.get('chat') != target_id
            or target_id not in authority.get('allowed_chat_ids', [])
            or not job.get('account_id') or job['account_id'] != authority.get('account_id')):
        raise ValueError('界面恢复未获当前账号和会话授权')
    ui.SKIP_MESSAGES = True
    result = reset_surface(ui)
    cache = path.parent / 'sticker-panel-anchors.cache'
    # Invalidate only computed positions. Favorites and sticker media stay intact.
    cache.write_text(json.dumps({'updated':0, 'anchors':{}}))
    job.update(result, state='recovered', anchors_reset=True)
    path.write_text(json.dumps(job, ensure_ascii=False))
