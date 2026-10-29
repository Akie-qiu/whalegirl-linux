"""Return native WeChat to its chat surface inside the gateway GUI guard."""
import json
import hashlib
from pathlib import Path
import re
import shutil
import time

ROOT = Path('/data/whalegirl-jobs')
STORAGE_WARNING_SIZE = (282, 310)
STORAGE_WARNING_RGB = 'ed4c15aaed191e7da91fb959233800984307d130badb3ec5853b379d68214fdc'


def active_modal(ui):
    """This client does not expose every modal through AT-SPI."""
    active=ui.run_process(['xdotool','getactivewindow'],capture_output=True,text=True,timeout=3)
    window=active.stdout.strip()
    if active.returncode or not window.isdigit():return None
    owner=ui.run_process(['xdotool','getwindowpid',window],capture_output=True,text=True,timeout=3)
    client=ui.run_process(['pgrep','-xo','wechat'],capture_output=True,text=True,timeout=3)
    if owner.returncode or client.returncode or owner.stdout.strip()!=client.stdout.strip():return None
    props=ui.run_process(['xprop','-id',window,'_NET_WM_STATE'],capture_output=True,text=True,timeout=3)
    if '_NET_WM_STATE_MODAL' not in props.stdout:return None
    geometry=ui.run_process(['xdotool','getwindowgeometry','--shell',window],capture_output=True,text=True,check=True,timeout=3)
    values=dict(re.findall(r'^(X|Y|WIDTH|HEIGHT)=(-?\d+)$',geometry.stdout,re.M))
    if len(values)!=4:raise ValueError('无法读取微信弹窗位置')
    return {'window':window,**{k:int(v) for k,v in values.items()}}


def resolve_modal(ui):
    modal=active_modal(ui)
    if modal is None:return False
    if (modal['WIDTH'],modal['HEIGHT'])!=STORAGE_WARNING_SIZE:
        raise ValueError('微信仍有弹窗遮挡聊天，需要先处理弹窗')
    if shutil.disk_usage(ROOT).free<1024**3:
        raise ValueError('磁盘空间仍不足，保留微信提示，请先释放至少1GB可用空间')
    capture=ui.run_process(['ffmpeg','-hide_banner','-loglevel','error','-f','x11grab','-draw_mouse','0',
        '-video_size','282x310','-i',':99.0+'+str(modal['X'])+','+str(modal['Y']),
        '-frames:v','1','-threads','1','-pix_fmt','rgb24','-f','rawvideo','pipe:1'],
        capture_output=True,check=True,timeout=5)
    if len(capture.stdout)!=282*310*3 or hashlib.sha256(capture.stdout).hexdigest()!=STORAGE_WARNING_RGB:
        raise ValueError('微信有未识别的弹窗，不能自动关闭，请先在微信中处理')
    # The verified pinned-client bitmap is the resolved storage warning only.
    # Its lower button dismisses today's notice without opening storage settings.
    ui.run_process(['/opt/tools/click',str(modal['X']+141),str(modal['Y']+261)],
                   check=True,timeout=3)
    time.sleep(.15)
    if active_modal(ui) is not None:
        raise ValueError('微信弹窗尚未关闭，聊天界面未恢复')
    return True


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
    storage_dismissed=resolve_modal(ui)
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
    if active_modal(ui) is not None:
        raise ValueError('微信仍有弹窗遮挡聊天，不能确认界面恢复')
    return {'main_ready': True,'storage_warning_dismissed':storage_dismissed}


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
