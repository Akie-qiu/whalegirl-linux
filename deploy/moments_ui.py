"""Bounded Moments UI actions, called only inside the existing gateway GUI guard."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import unicodedata

from chat_select_linux import tree, walk, native_read, click_node
from native_process import run as run_process

ROOT = Path('/data/whalegirl-jobs')
PUBLISH_ASSETS = Path('/data/moments-publish-assets')
FLAGS = {'publish': 'moments_publish_enabled', 'reply_own': 'moments_reply_own_enabled',
         'comment_friend': 'moments_comment_friends_enabled'}


def norm(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or '')))


def command(*args, **kwargs):
    return run_process(list(args), check=True, capture_output=True, timeout=4, **kwargs)


def clipboard(text):
    run_process(['xclip', '-selection', 'clipboard'], input=text.encode(),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=3)


def button(root, name, role='push-button'):
    values = [n for n in walk(root) if n.get('role') == role and n.get('name') == name]
    if len(values) != 1:
        raise ValueError('Native Moments control is not unique: ' + name)
    return values[0]


def frame(root=None):
    values = [n for n in walk(root or tree()) if n.get('role') == 'frame' and n.get('name') == 'Moments']
    if len(values) != 1:
        raise ValueError('Native Moments window is unavailable')
    return values[0]


def activate_moments():
    ids = command('xdotool', 'search', '--onlyvisible', '--name', '^Moments$').stdout.decode().split()
    if len(ids) != 1:
        raise ValueError('Native Moments window is not uniquely visible')
    command('xdotool', 'windowactivate', '--sync', ids[0])


def dismiss_comment_menu():
    # Escape without a menu closes Moments itself, leaving stale accessible rows.
    if any(n.get('role') in ('menu-item', 'popup-menu', 'menu') for n in walk(tree())):
        command('xdotool', 'key', '--clearmodifiers', 'Escape')


def wait_for(find, seconds=2, *, stage=None):
    end = time.monotonic() + seconds
    while True:
        try:
            return find(tree())
        except (ValueError, StopIteration):
            if time.monotonic() >= end:
                raise ValueError('Native Moments control did not become ready: ' + (stage or getattr(find, '__name__', 'control')))
            time.sleep(.12)


def editors(root):
    return [n for n in walk(root) if n.get('role') == 'text' and
            'EDITABLE' in (n.get('states') or []) and (n.get('bounds') or {}).get('width', 0) > 100]


def composer(root):
    f = frame(root)
    candidates = [n for n in walk(f) if editors(n) and any(
        c.get('role') == 'push-button' and c.get('name') == 'Cancel' for c in walk(n))]
    if not candidates:
        raise ValueError('Moments publish composer is unavailable')
    return min(candidates, key=lambda n: len(list(walk(n))))


def authorize(account, kind=None, payload=None):
    value = json.loads((ROOT / 'authorized-chats.json').read_text())
    if value.get('account_id') != account or value.get('moments_authorized') is not True:
        raise ValueError('Moments account is not authorized')
    if kind:
        if kind not in FLAGS or not value.get('moments_enabled') or not value.get(FLAGS[kind]):
            raise ValueError('This Moments action is disabled')
        if kind == 'comment_friend' and (payload or {}).get('post', {}).get('author') not in value.get('moments_friend_ids', []):
            raise ValueError('Friend is not authorized for Moments comments')
    # This also validates the currently logged-in account using the native reader.
    return native_read('/moments'), value


def open_moments():
    root = tree()
    # A failed favorite selection can leave this popup above the sidebar.
    # The first sidebar click would merely dismiss it and never open Moments.
    if any(n.get('role') == 'page-tab' and n.get('name') == 'Custom Stickers' for n in walk(root)):
        command('xdotool', 'key', '--clearmodifiers', 'Escape')
        time.sleep(.08)
        root = tree()
    existing = [n for n in walk(root) if n.get('role') == 'frame' and n.get('name') == 'Moments']
    if not existing:
        click_node(button(root, 'Moments'))
    wait_for(lambda r: frame(r), stage='open window')
    ids = command('xdotool', 'search', '--onlyvisible', '--name', '^Moments$').stdout.decode().split()
    if len(ids) != 1:
        raise ValueError('Native Moments window is not uniquely visible')
    command('xdotool', 'windowactivate', '--sync', ids[0])
    time.sleep(.1)
    if editors(frame()):
        raise ValueError('Moments has an existing editor; preserve the draft for manual handling')
    # Details -> Messages -> timeline can take two Back clicks. The old single
    # click left the next task in the notifications page with no Post control.
    for _ in range(4):
        f=frame()
        if any(n.get('role')=='push-button' and n.get('name')=='Messages' for n in walk(f)):
            break
        back=[n for n in walk(f) if n.get('role')=='push-button' and n.get('name')=='Back']
        if len(back)!=1:
            raise ValueError('Cannot return from Moments details to timeline')
        click_node(back[0])
        time.sleep(.12)
    wait_for(lambda r: button(frame(r), 'Messages'), stage='timeline')
    return frame()


def close_moments():
    try:
        f = frame()
        cancel = [n for n in walk(f) if n.get('role') == 'push-button' and n.get('name') == 'Cancel']
        if len(cancel) == 1:
            click_node(cancel[0])
        command('xdotool', 'key', '--clearmodifiers', 'Escape')
        click_node(button(frame(), 'Close'))
    except (ValueError, subprocess.SubprocessError):
        pass


def put_text(editor, text):
    click_node(editor)
    command('xdotool', 'key', '--clearmodifiers', 'ctrl+a')
    clipboard(text)
    command('xdotool', 'key', '--clearmodifiers', 'ctrl+v')
    time.sleep(.12)


def image_file_dialog(root):
    dialogs = [n for n in walk(root) if n.get('role') == 'dialog' and n.get('name') == 'Open']
    if len(dialogs) != 1:
        raise ValueError('Moments image file dialog is not unique')
    return dialogs[0]


def select_publish_images(paths):
    dialog = wait_for(image_file_dialog, stage='image file dialog')
    # Qt can restore this modal to coordinates beyond the Xvfb screen. The
    # accessible Open button still exists there, but a pointer click misses it.
    width, height = map(int, command('xdotool', 'getdisplaygeometry').stdout.split())
    bounds = dialog['bounds']
    if bounds['width'] > width or bounds['height'] > height:
        raise ValueError('Moments image file dialog is larger than the display')
    windows = command('xdotool', 'search', '--onlyvisible', '--name', '^Open$').stdout.decode().split()
    if len(windows) != 1:
        raise ValueError('Moments image file window is not unique')
    command('xdotool', 'windowactivate', '--sync', windows[0])
    command('xdotool', 'windowmove', '--sync', windows[0],
            str((width-bounds['width'])//2), str((height-bounds['height'])//2))

    def visible_dialog(root):
        current = image_file_dialog(root)
        b = current['bounds']
        if b['x'] < 0 or b['y'] < 0 or b['x']+b['width'] > width or b['y']+b['height'] > height:
            raise ValueError('Moments image file dialog is outside the display')
        return current

    dialog = wait_for(visible_dialog, stage='image file dialog placement')
    fields = [n for n in walk(dialog) if n.get('role') == 'text' and
              n.get('name') == 'File name:' and 'EDITABLE' in (n.get('states') or [])]
    if len(fields) != 1:
        raise ValueError('Moments image file name field is not unique')
    put_text(fields[0], ' '.join('"' + p + '"' for p in paths))

    def ready_open(root):
        control = button(visible_dialog(root), 'Open')
        if 'DISABLED' in (control.get('states') or []):
            raise ValueError('Moments image files are not ready')
        return control

    click_node(wait_for(ready_open, stage='image file selection'))


def readable_publish_images(paths):
    # Job files are private to the core account. The native desktop runs as a
    # different user and cannot traverse that directory, even for 0644 PNGs.
    # Copy only the authorized image inputs into a separate readable directory.
    PUBLISH_ASSETS.mkdir(mode=0o755, exist_ok=True)
    PUBLISH_ASSETS.chmod(0o755)
    result = []
    for path in paths:
        source = Path(path)
        destination = PUBLISH_ASSETS/source.name
        pending = destination.with_suffix('.pending')
        shutil.copyfile(source, pending)
        pending.chmod(0o644)
        pending.replace(destination)
        result.append(str(destination))
    return result


def prepare_publish(payload):
    f = frame()
    post = button(f, 'Post')
    assets = payload.get('assets') or []
    if assets:
        if len(assets) > 9:
            raise ValueError('Too many Moments images')
        paths = []
        for identity in assets:
            if not re.fullmatch(r'[0-9a-f]{64}', identity):
                raise ValueError('Invalid Moments image identity')
            p = ROOT / 'moments-assets' / (identity + '.png')
            if not p.is_file():
                raise ValueError('Moments image is unavailable')
            paths.append(str(p))
        click_node(post)
        select_publish_images(readable_publish_images(paths))
    else:
        b = post['bounds']
        command('xdotool', 'mousemove', str(round(b['x'] + b['width']/2)), str(round(b['y'] + b['height']/2)), 'click', '3')
        menu = wait_for(lambda r: button(r, 'Post Text', 'menu-item'), stage='Post Text menu')
        click_node(menu)
    return wait_for(composer, stage='publish editor')


def matching_post(snapshot, source, kind):
    target = source.get('post') or {}
    rows = [p for p in snapshot['posts'] if p['id'] == target.get('id') and p['author'] == target.get('author')]
    if len(rows) != 1 or rows[0]['text'] != target.get('text'):
        raise ValueError('Source Moments post changed or is no longer cached')
    post = rows[0]
    if (kind == 'reply_own') != bool(post['own']):
        raise ValueError('Moments owner does not match action type')
    if kind == 'reply_own':
        expected = source.get('comment') or {}
        comments = [c for c in post['comments'] if c['id'] == expected.get('id') and c['author'] == expected.get('author')
                    and c['text'] == expected.get('text') and not c.get('own')]
        if len(comments) != 1:
            raise ValueError('Source comment changed or is no longer available')
    return post


def locate_post(post, snapshot, *, allow_partial=False, allow_bottom=False):
    text = norm(post.get('text') or post.get('title'))
    if len(text) < 4:
        raise ValueError('Post has no unique readable text; do not guess an image-only target')
    if sum(norm(p.get('text') or p.get('title')) == text for p in snapshot['posts']) != 1:
        raise ValueError('Duplicate post text prevents reliable UI targeting')
    initial_lists = [n for n in walk(frame()) if n.get('role') == 'list']
    if len(initial_lists) != 1:
        raise ValueError('Native Moments list is not unique')
    a = initial_lists[0]['bounds']
    command('xdotool', 'mousemove', str(round(a['x']+a['width']/2)), str(round(a['y']+a['height']/2)),
            'click', '--repeat', '30', '--delay', '3', '4')
    time.sleep(.12)
    for _ in range(7):
        f = frame()
        lists = [n for n in walk(f) if n.get('role') == 'list']
        if len(lists) != 1:
            raise ValueError('Native Moments list is not unique')
        area = lists[0]['bounds']
        rows = [n for n in lists[0].get('children', []) if n.get('role') == 'list-item'
                and text in norm(n.get('name'))]
        repeat = '3'
        if len(rows) == 1:
            b = rows[0]['bounds']
            if b['y'] >= area['y'] and b['y']+b['height'] <= area['y']+area['height']:
                return rows[0]
            if allow_bottom and area['y']+40 <= b['y']+b['height'] <= area['y']+area['height']:
                return rows[0]
            if allow_partial and b['y'] < area['y']+area['height'] and b['y']+b['height'] > area['y']:
                # A tall post cannot fit in one viewport. Its unique text still
                # binds the following comment block; selection scrolls that block.
                return rows[0]
            if 0 < (b['y']+b['height']) - (area['y']+area['height']) <= 180:
                repeat = '1'
        command('xdotool', 'mousemove', str(round(area['x']+area['width']/2)), str(round(area['y']+area['height']/2)),
                'click', '--repeat', repeat, '--delay', '40', '5')
        time.sleep(.1)
    raise ValueError('Exact source post is not fully visible within bounded history')


def capture_area(bounds, name):
    import struct
    activate_moments()
    path = ROOT/(name+'.png')
    pending = ROOT/(name+'.capture-'+str(os.getpid())+'-'+str(time.monotonic_ns())+'.png')
    command('scrot', '-a', ','.join(str(bounds[k]) for k in ('x', 'y', 'width', 'height')), str(pending))
    header = pending.read_bytes()[:24]
    if header[:8] != b'\x89PNG\r\n\x1a\n' or struct.unpack('>II', header[16:24]) != (bounds['width'], bounds['height']):
        raise ValueError('Native Moments capture dimensions do not match the current viewport')
    pending.chmod(0o600)
    pending.replace(path)
    path.chmod(0o600)
    return path


def copied_comment_matches(value, target):
    expected = norm(target['text'])
    return norm(value) == expected or norm(value) in {
        norm(target.get('name', '')+separator+target['text']) for separator in (':', '：')}


def comment_detail(post,expected_height=None):
    f=frame()
    lists=[n for n in walk(f) if n.get('role')=='list']
    if len(lists)!=1:
        raise ValueError('Comment detail list is not unique')
    rows=lists[0].get('children',[])
    owners=[i for i,n in enumerate(rows) if norm(post['text']) in norm(n.get('name'))]
    if len(owners)==1 and owners[0]+1<len(rows):
        return rows[owners[0]+1]['bounds'],lists[0]['bounds']
    # Qt removes the off-screen post accessible after scrolling. The opaque
    # comment block keeps its full logical height, previously bound to this post.
    if expected_height is not None:
        matches=[n for n in rows if n.get('role')=='list-item' and
                 (n.get('bounds') or {}).get('width')==548 and
                 (n.get('bounds') or {}).get('height')==expected_height]
        if len(matches)==1:return matches[0]['bounds'],lists[0]['bounds']
    raise ValueError('Source post is not confirmed in comment detail')


def select_scrolling_comment(post,target):
    """Search visible row bands; native Copy confirms the exact unique source."""
    from moments_layout import visible_comment_points
    if sum(norm(c['text'])==norm(target['text']) for c in post['comments'])!=1:
        raise ValueError('Duplicate comment text needs an unambiguous native row; no reply sent')
    ordered=sorted(post['comments'],key=lambda c:(c['created'],int(c['id'])))
    index=next(i for i,c in enumerate(ordered) if c['id']==target['id'])
    direction='4' if index>=len(ordered)/2 else '5'
    initial='5' if direction=='4' else '4'
    bounds,area=comment_detail(post)
    expected_height=bounds['height']
    command('xdotool','mousemove',str(area['x']+area['width']//2),str(area['y']+area['height']//2),
            'click','--repeat','25','--delay','3',initial)
    time.sleep(.12)
    seen=set()
    deadline=time.monotonic()+20
    for _ in range(8):
        bounds,area=comment_detail(post,expected_height)
        top,bottom=max(bounds['y'],area['y']),min(bounds['y']+bounds['height'],area['y']+area['height'])
        if bottom<=top:
            raise ValueError('Comment region is outside native viewport')
        clip={'x':bounds['x'],'y':top,'width':bounds['width'],'height':bottom-top}
        points=visible_comment_points(capture_area(clip,'moments-comment-visible'),top==bounds['y'])
        if direction=='4':points.reverse()
        for offset in points:
            if time.monotonic()>deadline:
                raise ValueError('Exact source comment was not located within bounded scrolling')
            x,y=bounds['x']+160,top+offset
            relative=y-bounds['y']
            if any(abs(relative-old)<14 for old in seen):continue
            seen.add(relative)
            activate_moments()
            clipboard('whalegirl-comment-verification')
            command('xdotool','mousemove',str(x),str(y),'click','3')
            try:
                copied_control=wait_for(lambda r:button(r,'Copy','menu-item'),seconds=.35)
            except ValueError:
                dismiss_comment_menu()
                continue
            click_node(copied_control)
            copied=command('xclip','-selection','clipboard','-o').stdout.decode()
            if copied_comment_matches(copied,target):
                return x,y
        before=bounds['y']
        activate_moments()
        command('xdotool','mousemove',str(area['x']+area['width']//2),str(area['y']+area['height']//2),
                'click','--repeat','4','--delay','15',direction)
        time.sleep(.12)
        if comment_detail(post,expected_height)[0]['y']==before:break
    raise ValueError('Exact source comment is not present in visible native history')


def select_timeline_comment(post, target):
    """Bind the source post first; use native Copy to identify its actual comment."""
    from moments_layout import timeline_comment_points
    ordered = sorted(post['comments'], key=lambda c: (c['created'], int(c['id'])))
    index = next(i for i, c in enumerate(ordered) if c['id'] == target['id'])
    duplicate = sum(norm(c['text']) == norm(target['text']) for c in post['comments']) != 1
    seen = set()
    deadline = time.monotonic()+18
    expected_height = None
    for _ in range(8):
        bounds, area = comment_detail(post, expected_height)
        if expected_height is None:
            expected_height = bounds['height']
        if duplicate and bounds['height'] > area['height']:
            raise ValueError('Duplicate comment text needs an unambiguous native row; no reply sent')
        top, bottom = max(bounds['y'], area['y']), min(bounds['y']+bounds['height'], area['y']+area['height'])
        full_view = bounds['y'] >= area['y'] and bounds['y']+bounds['height'] <= area['y']+area['height']
        if bottom > top and (not duplicate or full_view):
            clip = {'x': bounds['x'], 'y': top, 'width': bounds['width'], 'height': bottom-top}
            points = timeline_comment_points(capture_area(clip, 'moments-timeline-comments'))
            if duplicate:
                if len(points) != len(ordered):
                    raise ValueError('Duplicate comment text needs an unambiguous native row; no reply sent')
                points = [points[index]]
            for offset in points:
                if time.monotonic() > deadline:
                    raise ValueError('Exact source comment was not located within bounded scrolling')
                x, y = bounds['x']+160, top+offset
                if not top <= y < bottom:
                    raise ValueError('Native comment point is outside the current viewport')
                relative = y-bounds['y']
                if any(abs(relative-old) < 6 for old in seen):
                    continue
                seen.add(relative)
                activate_moments()
                clipboard('whalegirl-comment-verification')
                command('xdotool', 'mousemove', str(x), str(y), 'click', '3')
                try:
                    copy = wait_for(lambda r: button(r, 'Copy', 'menu-item'), seconds=.35)
                except ValueError:
                    dismiss_comment_menu()
                    continue
                click_node(copy)
                copied = command('xclip', '-selection', 'clipboard', '-o').stdout.decode()
                if copied_comment_matches(copied, target):
                    return x, y
        if bounds['y']+bounds['height'] <= area['y']+area['height']:
            break
        activate_moments()
        command('xdotool', 'mousemove', str(area['x']+area['width']//2),
                str(area['y']+area['height']//2), 'click', '--repeat', '2', '--delay', '20', '5')
        time.sleep(.12)
    raise ValueError('Exact source comment is not present in visible native history')


def select_notification_comment(post, target):
    """Retain the verified detail view for comments unavailable in the timeline."""
    from moments_layout import comment_bands
    click_node(button(frame(), 'Messages'))
    def notification(root):
        rows = []
        def visit(node, viewport=None):
            if node.get('role') in ('list', 'scroll-pane') and node.get('bounds'):
                viewport = node['bounds']
            if node.get('role') == 'list-item' and all(norm(value) in norm(node.get('name')) for value in
                    (target['text'], target.get('name', ''))):
                rows.append((node, viewport or node['bounds']))
            for child in node.get('children', []):
                visit(child, viewport)
        visit(root)
        if len(rows) != 1:
            raise ValueError('Source comment is unavailable in both timeline and notification history')
        return rows[0]
    # The notification preview contains the author and comment, plus a thumbnail;
    # it does not contain the source post body. Verify that body in detail instead.
    for _ in range(6):
        row, area = wait_for(notification, stage='notification')
        bounds = row['bounds']
        if bounds['y'] >= area['y'] and bounds['y']+bounds['height'] <= area['y']+area['height']:
            click_node(row)
            break
        direction = '4' if bounds['y'] < area['y'] else '5'
        command('xdotool', 'mousemove', str(area['x']+area['width']//2),
                str(area['y']+area['height']//2), 'click', '--repeat', '2', '--delay', '20', direction)
        time.sleep(.1)
    else:
        raise ValueError('Source notification is outside the visible popup')
    wait_for(lambda r: button(frame(r), 'Back'))
    bounds, area = comment_detail(post)
    expected_height = bounds['height']
    for _ in range(6):
        if bounds['y'] >= area['y'] and bounds['y']+bounds['height'] <= area['y']+area['height']:
            break
        if bounds['height'] > area['height'] or bounds['y'] <= area['y']:
            break
        activate_moments()
        repeat = '1' if (bounds['y']+bounds['height']) - (area['y']+area['height']) <= 140 else '2'
        command('xdotool', 'mousemove', str(area['x']+area['width']//2),
                str(area['y']+area['height']//2), 'click', '--repeat', repeat, '--delay', '20', '5')
        time.sleep(.12)
        bounds, area = comment_detail(post, expected_height)
    ordered = sorted(post['comments'], key=lambda c: (c['created'], int(c['id'])))
    index = next(i for i, c in enumerate(ordered) if c['id'] == target['id'])
    if bounds['y'] >= area['y'] and bounds['y']+bounds['height'] <= area['y']+area['height']:
        bands = comment_bands(capture_area(bounds, 'moments-comment-region'), len(ordered))
        x, y = bounds['x']+160, bounds['y']+bands[index][1]-16
        clipboard('whalegirl-comment-verification')
        command('xdotool', 'mousemove', str(x), str(y), 'click', '3')
        click_node(wait_for(lambda r: button(r, 'Copy', 'menu-item')))
        copied = command('xclip', '-selection', 'clipboard', '-o').stdout.decode()
        if not copied_comment_matches(copied, target):
            raise ValueError('Copied native comment does not match exact source and recipient')
        return x, y
    return select_scrolling_comment(post, target)


def reply_comment(post, source, *, snapshot=None):
    """Reply directly under the verified post; notifications are not durable history."""
    target = source['comment']
    try:
        if snapshot is not None:
            locate_post(post, snapshot, allow_partial=True)
        x, y = select_timeline_comment(post, target)
    except ValueError as primary:
        try:
            x, y = select_notification_comment(post, target)
        except ValueError as secondary:
            raise ValueError('Timeline: '+str(primary)+'; notification: '+str(secondary)) from None
    def menu():
        command('xdotool', 'mousemove', str(x), str(y), 'click', '3')
    fresh = native_read('/moments')
    current = next((p for p in fresh['posts'] if p['id'] == post['id']), {})
    def signature(values):
        prefix = []
        for c in sorted(values, key=lambda c: (c['created'], int(c['id']))):
            prefix.append((c['id'], c['author'], c['text']))
            if c['id'] == target['id']:
                return prefix
        return None
    # Only changes at/before the selected row can invalidate its coordinates.
    # New replies appended below it should not abort an otherwise exact match.
    if signature(current.get('comments', [])) != signature(post['comments']):
        raise ValueError('Comment order changed before selecting Reply')
    activate_moments()
    menu()
    click_node(wait_for(lambda r: button(r, 'Reply', 'menu-item')))
    return {'native_comment': True}


def put_comment_text(text):
    from moments_layout import reply_box
    clipboard(text)
    command('xdotool', 'key', '--clearmodifiers', 'ctrl+a', 'ctrl+v')
    clipboard('whalegirl-editor-verification')
    command('xdotool', 'key', '--clearmodifiers', 'ctrl+a', 'ctrl+c')
    copied = command('xclip', '-selection', 'clipboard', '-o').stdout.decode()
    if copied != text:
        raise ValueError('Native comment editor content did not match the reply')
    command('xdotool', 'key', '--clearmodifiers', 'ctrl+End')
    bounds = frame()['bounds']
    try:
        box = reply_box(capture_area(bounds, 'moments-reply-composer'))
    except ValueError:
        command('xdotool', 'mousemove', str(bounds['x']+bounds['width']//2),
                str(bounds['y']+bounds['height']//2), 'click', '--repeat', '2', '--delay', '20', '5')
        time.sleep(.12)
        box = reply_box(capture_area(bounds, 'moments-reply-composer'))
    return (bounds['x']+box['x']+box['width']-38,
            bounds['y']+box['y']+box['height']-24)


def comment_editor(post, source, kind, snapshot):
    if kind == 'reply_own':
        return reply_comment(post, source, snapshot=snapshot)
    row = locate_post(post, snapshot, allow_bottom=True)
    b = row['bounds']
    activate_moments()
    # Measured on the pinned 4.1.1.8 layout; popup is a top-level accessible under root.
    command('/opt/tools/click', str(round(b['x']+b['width']-40)), str(round(b['y']+b['height']-10)))
    click_node(wait_for(lambda r: button(r, 'Comment'), stage='Comment menu'))
    return {'native_comment': True}


def reply_navigation_summary(post, target):
    """Private diagnostic flags, without copying timeline text into logs."""
    root = tree()
    f = frame(root)
    lists = [n for n in walk(f) if n.get('role') == 'list']
    return {
        'buttons': [{'name': n.get('name'), 'bounds': n.get('bounds')}
                    for n in walk(f) if n.get('role') == 'push-button' and n.get('name')],
        'lists': [{'bounds': n.get('bounds'), 'rows': [
            {'bounds': row.get('bounds'), 'length': len(row.get('name', '')),
             'post_match': norm(post.get('text')) in norm(row.get('name')),
             'post_prefix_match': norm(post.get('text'))[:20] in norm(row.get('name')),
             'comment_match': norm(target.get('text')) in norm(row.get('name')),
             'author_match': norm(target.get('name')) in norm(row.get('name'))}
            for row in n.get('children', []) if row.get('role') == 'list-item']}
            for n in lists],
        'matching_nodes': [
            {'role': n.get('role'), 'bounds': n.get('bounds'), 'length': len(n.get('name', '')),
             'comment_match': norm(target.get('text')) in norm(n.get('name')),
             'author_match': norm(target.get('name')) in norm(n.get('name'))}
            for n in walk(root) if n.get('name') and (
                norm(target.get('text')) in norm(n.get('name')) or
                norm(target.get('name')) in norm(n.get('name')))],
    }


def run(path, account):
    import chat_select_linux as native_ui
    native_ui.SKIP_MESSAGES = True
    job = json.loads(path.read_text())
    operation = job.get('operation')
    if operation not in ('moments_read', 'moments_probe', 'moments_reply_probe', 'moments_send') or time.time()-float(job.get('created', 0)) > 60:
        raise ValueError('Invalid or stale Moments job')
    if job.get('state'):
        raise ValueError('Moments jobs cannot be replayed')
    sending = operation == 'moments_send'
    kind = job.get('kind') if sending else None
    source = job.get('source') or {}
    snapshot, authority = authorize(account, kind, source)
    opened = False
    try:
        open_moments()
        opened = True
        if operation == 'moments_reply_probe':
            # Exercise selection through the existing GUI guard, never type or submit.
            post = matching_post(snapshot, source, 'reply_own')
            diagnostic = {'initial': reply_navigation_summary(post, source['comment'])}
            try:
                comment_editor(post, source, 'reply_own', snapshot)
                diagnostic['target_verified'] = True
            except ValueError as exc:
                diagnostic.update(target_verified=False, error=str(exc),
                                  final=reply_navigation_summary(post, source['comment']))
                capture_area(frame()['bounds'], 'moments-reply-navigation')
                command('scrot', str(ROOT/'moments-reply-desktop.png'))
                (ROOT/'moments-reply-desktop.png').chmod(0o600)
            job.update(state='done', diagnostic=diagnostic)
        elif not sending:
            click_node(button(frame(), 'Refresh'))
            time.sleep(.5)
            snapshot, _ = authorize(account)
            capabilities = {'read': True, 'image_publish': True,
                            'comment_friend': True, 'reply_own': True, 'delivery_verified': False}
            if operation == 'moments_probe':
                capabilities['text_publish'] = False
                try:
                    prepare_publish({})
                    capabilities['text_publish'] = True
                    click_node(button(composer(tree()), 'Cancel'))
                except ValueError:
                    pass
            job.update(snapshot, capabilities=capabilities, state='done')
        else:
            text = job.get('text')
            if not isinstance(text, str) or not text.strip() or len(text) > (180 if kind == 'publish' else 100):
                raise ValueError('Invalid Moments text')
            if kind == 'publish':
                image_authority = json.loads((ROOT/'image-assets.json').read_text()) if (ROOT/'image-assets.json').exists() else {}
                approved = set(authority.get('moments_asset_ids', []))
                if image_authority.get('account_id') == authority.get('account_id'):
                    approved.update(image_authority.get('assets', []))
                if any(a not in approved for a in source.get('assets', [])):
                    raise ValueError('Moments image is not enabled for publishing')
                container = prepare_publish(source)
                fields = editors(container)
                if len(fields) != 1:
                    raise ValueError('Moments publish editor is not unique')
                put_text(fields[0], text)
                submit = button(composer(tree()), 'Post')
                before = {p['id'] for p in snapshot['posts'] if p['own']}
            else:
                post = matching_post(snapshot, source, kind)
                editor = comment_editor(post, source, kind, snapshot)
                comment_submit = put_comment_text(text) if editor.get('native_comment') else None
                if not comment_submit:
                    put_text(editor, text)
                before = {c['id'] for c in post['comments']}
                submit = None
            # Recheck switches/account immediately before the non-idempotent click.
            authorize(account, kind, source)
            job['state'] = 'sending'
            path.write_text(json.dumps(job, ensure_ascii=False))
            if submit:
                click_node(submit)
            elif comment_submit:
                command('/opt/tools/click', *[str(v) for v in comment_submit])
            else:
                command('xdotool', 'key', '--clearmodifiers', 'Return')
            job.update(state='submitted', confirmed=False)
            path.write_text(json.dumps(job, ensure_ascii=False))
            end = time.monotonic()+5
            while time.monotonic() < end:
                latest = native_read('/moments')
                if kind == 'publish':
                    matches = [p for p in latest['posts'] if p['own'] and p['id'] not in before and p['text'] == text]
                else:
                    target = next((p for p in latest['posts'] if p['id'] == post['id']), {})
                    matches = [c for c in target.get('comments', []) if c.get('own') and not c.get('local')
                               and c['id'] not in before and c['text'] == text
                               and (kind != 'reply_own' or c.get('reply_to') == source['comment']['id'])]
                if len(matches) == 1:
                    job.update(confirmed=True, receipt=matches[0]['id'])
                    break
                time.sleep(.25)
        path.write_text(json.dumps(job, ensure_ascii=False))
    finally:
        if opened:
            close_moments()
