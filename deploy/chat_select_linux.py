#!/usr/bin/env python3
"""Select a uniquely named native Linux chat while the gateway owns its GUI guard."""
import base64
import json
import http.client
import os
from pathlib import Path
import re
import subprocess
import socket
import sys
import time
import unicodedata
import urllib.request
from urllib.parse import urlencode

sys.path.insert(0, '/data/native-tools')
from native_process import run as run_process
SKIP_MESSAGES = False
PROFILE = {'snapshots': 0, 'snapshot_seconds': 0.0}


def walk(node):
    yield node
    for child in node.get("children") or []:
        yield from walk(child)


def normal(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def tree():
    started = time.monotonic()
    command = ["/opt/tools/a11y-dump", "--format", "json"]
    if SKIP_MESSAGES:
        command.append('--skip-messages')
    result = run_process(command, capture_output=True, text=True, check=True, timeout=8)
    PROFILE['snapshots'] += 1
    PROFILE['snapshot_seconds'] += time.monotonic() - started
    value = json.loads(result.stdout)
    return value.get("tree", value)


def chats():
    token = Path("/data/auth-token").read_text().strip()
    request = urllib.request.Request("http://127.0.0.1:6174/api/chats?limit=1000&offset=0", headers={"Authorization": "Bearer " + token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=10) as response:
        return json.load(response)


def header_matches(value, display_name, is_group):
    value = normal(value)
    if value == display_name:
        return True
    # Native group titles can append a changing member count.
    return is_group and re.fullmatch(re.escape(display_name) + r"\s*[（(]\s*\d+\s*[)）]", value) is not None


def selected(root, display_name, is_group=False):
    nodes = list(walk(root))
    lists = [n for n in nodes if n.get("role") == "list" and n.get("name") == "Chats"]
    if len(lists) != 1:
        return False
    bounds = lists[0].get("bounds") or {}
    right = bounds.get("x", 0) + bounds.get("width", 0)
    chosen = [n for n in lists[0].get("children") or [] if "SELECTED" in (n.get("states") or [])]
    matching_row = any(normal(n.get("name")) == display_name or normal(n.get("name")).startswith(display_name + " ") for n in chosen)
    header = any(n.get("role") == "label" and header_matches(n.get("name"), display_name, is_group)
                 and (n.get("bounds") or {}).get("x", -1) > right
                 and (n.get("bounds") or {}).get("y", 1000) < 100 for n in nodes)
    return matching_row and header


def preview_windows():
    result = run_process(['xdotool', 'search', '--onlyvisible', '--name', '^Photos and Videos$'],
                            capture_output=True, text=True, timeout=3)
    if result.returncode not in (0, 1):
        raise ValueError('Cannot inspect native image preview')
    return set(result.stdout.split())


def close_previews(windows):
    for window in windows:
        run_process(['xdotool', 'windowactivate', '--sync', window], check=True, timeout=3)
        run_process(['xdotool', 'key', '--clearmodifiers', 'alt+F4'], check=True, timeout=3)
    if windows:
        deadline=time.monotonic()+1.5
        while preview_windows().intersection(windows) and time.monotonic()<deadline:
            time.sleep(.1)
        if preview_windows().intersection(windows):
            raise ValueError('Native image preview did not close')


def click_node(node):
    b=node['bounds']
    run_process(['/opt/tools/click',str(round(b['x']+b['width']/2)),str(round(b['y']+b['height']/2))],
                   check=True,stdout=subprocess.DEVNULL,timeout=3)


def native_read(route, **params):
    jobs = Path('/data/whalegirl-jobs')
    endpoint = (jobs / 'native-read.endpoint').read_text().strip()
    if not re.fullmatch(r'native-read-\d+\.sock', endpoint):
        raise ValueError('Native read endpoint is invalid')
    connection = http.client.HTTPConnection('localhost', timeout=6)
    connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.sock.settimeout(6)
    try:
        connection.sock.connect(str(jobs / endpoint))
        connection.request('GET', route + ('?' + urlencode(params) if params else ''))
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('Native message reader rejected the request')
        return json.loads(response.read(2 * 1024 * 1024))
    finally:
        connection.close()


def quote_target_node(job, root):
    """Map a stable native message ID to its currently visible incoming text row."""
    from difflib import SequenceMatcher
    target = job.get('quote_to') or {}
    local_id = int(target.get('local_id') or 0)
    if local_id <= 0 or not str(target.get('server_id', '')).isdigit():
        raise ValueError('Quote target has no stable native identity')
    rows = sorted(native_read('/messages', chat=job['chat'], limit=100)['messages'], key=lambda m: (m['timestamp'], m['localId']))
    original = next((m for m in rows if m['localId'] == local_id), None)
    if (not original or original.get('type') != 1 or original.get('isSelf') is not False or
            str(original.get('serverId')) != str(target['server_id']) or original['content'] != target.get('content')):
        raise ValueError('Quote target no longer matches the incoming message')
    lists = [n for n in walk(root) if n.get('role') == 'list' and n.get('name') == 'Messages']
    if len(lists) != 1:
        raise ValueError('Quote message list is not unique')
    text = lambda value: re.sub(r'\s+', ' ', normal(value)).strip()
    nodes = [n for n in lists[0].get('children') or [] if n.get('role') == 'list-item'
             and not re.fullmatch(r'\d{1,2}:\d{2}', text(n.get('name')))]
    db_text = [text(m.get('content')) for m in rows]
    ui_text = [text(n.get('name')) for n in nodes]
    target_index = next(i for i, m in enumerate(rows) if m['localId'] == local_id)
    candidates = [n for n in nodes if text(n.get('name')) == text(original['content'])]
    chosen = candidates[0] if len(candidates) == 1 and db_text.count(text(original['content'])) == 1 else None
    if chosen is None:
        for block in SequenceMatcher(None, db_text, ui_text, autojunk=False).get_matching_blocks():
            anchors = db_text[block.a:block.a + block.size]
            if (block.a <= target_index < block.a + block.size and block.size >= 2 and
                    any(len(a) > 1 and db_text.count(a) == 1 for a in anchors)):
                chosen = nodes[block.b + target_index - block.a]
                break
    if chosen is None:
        raise ValueError('Quote target is outside the visible recent messages')
    area = lists[0]['bounds']; bounds = chosen['bounds']
    if bounds['y'] < area['y'] or bounds['y'] + bounds['height'] > area['y'] + area['height']:
        raise ValueError('Quote target is partially outside the message viewport')
    return chosen, original


def quote_context_menu(job, root):
    for attempt in range(3):
        try:
            chosen, original = quote_target_node(job, root)
            break
        except ValueError:
            if attempt == 2:
                raise
            if attempt == 0:
                lists = [n for n in walk(root) if n.get('role') == 'list' and n.get('name') == 'Messages']
                if len(lists) == 1:
                    b = lists[0]['bounds']
                    run_process(['xdotool','mousemove',str(round(b['x']+b['width']/2)),
                                    str(round(b['y']+b['height']/2)),'click','--repeat','40','--delay','5','5'],check=True,timeout=3)
            time.sleep(.2)
            root = tree()
    b = chosen['bounds']
    run_process(['xdotool', 'mousemove', str(round(b['x'] + 80)),
                    str(round(b['y'] + b['height'] - 20)), 'click', '3'], check=True, timeout=3)
    return tree(), original


def prepare_quote(job, root):
    menu, original = quote_context_menu(job, root)
    choices = [n for n in walk(menu) if n.get('role') in ('menu-item','push-button')
               and normal(n.get('name')) in ('Quote','Reply','引用','引用回复')]
    if len(choices) != 1:
        run_process(['xdotool','key','--clearmodifiers','Escape'],check=True,timeout=3)
        raise ValueError('Native Quote action is unavailable for this message')
    click_node(choices[0])
    job['quote_applied'] = True
    job['quoted_server_id'] = str(original['serverId'])
    return tree()


def collect_native_sticker(job,path,root,name,is_group):
    md5=job.get('md5','');local_id=int(job.get('local_id') or 0);server_id=str(job.get('server_id',''))
    if not re.fullmatch('[0-9a-f]{32}',md5) or local_id<=0 or not server_id.isdigit():
        raise ValueError('Invalid incoming native sticker identity')
    if md5 in {r['md5'] for r in native_read('/stickers')['stickers']}:
        job['state']='collected';return
    sys.path.insert(0,'/data/native-tools')
    from media_locator import locate_image
    raw=sorted(native_read('/messages',chat=job['chat'],limit=200)['messages'],key=lambda r:(r['timestamp'],r['localId']))
    matches=[r for r in raw if r['localId']==local_id and str(r.get('serverId'))==server_id and
             r.get('type')==47 and r.get('isSelf') is False and (r.get('sticker') or {}).get('md5')==md5]
    if len(matches)!=1:raise ValueError('Source sticker identity no longer matches the incoming message')
    moved=False
    try:
        for attempt in range(4):
            lists=[n for n in walk(root) if n.get('role')=='list' and n.get('name')=='Messages']
            if len(lists)!=1:raise ValueError('Message viewport is not unique')
            area=lists[0]['bounds'];nodes=[n for n in lists[0].get('children',[]) if n.get('role')=='list-item']
            chosen=locate_image(raw,nodes,local_id,47,server_id)
            if chosen:
                b=chosen['bounds'];top=max(area['y'],b['y']);bottom=min(area['y']+area['height'],b['y']+b['height'])
                if bottom-top>=35:break
            if attempt==3:raise ValueError('Source sticker is outside the visible recent messages')
            run_process(['xdotool','mousemove',str(round(area['x']+area['width']/2)),str(round(area['y']+area['height']/2)),
                'click','--repeat','40' if attempt==0 else '4','--delay','5' if attempt==0 else '65','5' if attempt==0 else '4'],check=True,timeout=3)
            moved=True;time.sleep(.12);root=tree()
        if not selected(root,name,is_group):raise ValueError('Destination changed before collecting')
        run_process(['xdotool','mousemove',str(round(b['x']+min(80,b['width']/3))),str(round((top+bottom)/2)),'click','3'],check=True,timeout=3)
        menu=tree()
        choices=[n for n in walk(menu) if n.get('role')=='menu-item' and n.get('name')=='Add Sticker']
        if len(choices)!=1:raise ValueError('Native Add Sticker action is unavailable')
        job['state']='collecting';path.write_text(json.dumps(job,ensure_ascii=False))
        # This menu only adds a favorite. Never select Forward, Delete or a send tile.
        click_node(choices[0])
        for _ in range(6):
            time.sleep(.2)
            if md5 in {r['md5'] for r in native_read('/stickers')['stickers']}:
                job['state']='collected';return
        raise ValueError('Native favorite did not appear after Add Sticker')
    finally:
        run_process(['xdotool','key','--clearmodifiers','Escape'],check=True,timeout=3)
        if moved:
            run_process(['xdotool','mousemove',str(round(area['x']+area['width']/2)),str(round(area['y']+area['height']/2)),
                'click','--repeat','40','--delay','5','5'],check=True,timeout=3)


def send_native_sticker(job,path,root,name,is_group):
    try:
        return _send_native_sticker(job,path,root,name,is_group)
    finally:
        # Always dismiss the popup, including errors before clicking a tile.
        # Otherwise the next chat/Moments click only closes this overlay.
        run_process(['xdotool','key','--clearmodifiers','Escape'],check=True,timeout=3)


def _send_native_sticker(job,path,root,name,is_group):
    started=time.monotonic()
    md5=job.get('md5','')
    if not re.fullmatch(r'[0-9a-f]{32}',md5):raise ValueError('Invalid sticker identity')
    order=[r['md5'] for r in native_read('/stickers')['stickers']]
    if order.count(md5)!=1:raise ValueError('Requested sticker is not in native favorites')
    if not any(n.get('role')=='page-tab' and n.get('name')=='Custom Stickers' for n in walk(root)):
        buttons=[n for n in walk(root) if n.get('role')=='push-button' and n.get('name')=='Send sticker(Alt+E)']
        if len(buttons)!=1:raise ValueError('Sticker button is not unique')
        click_node(buttons[0]);time.sleep(.1);root=tree()
    index=order.index(md5)
    def panel(root):
        tiles=sorted([n for n in walk(root) if n.get('role')=='label' and n.get('name')=='Custom Stickers'
                      and (n.get('bounds') or {}).get('width',0)>50],key=lambda n:(n['bounds']['y'],n['bounds']['x']))
        lists=[n for n in walk(root) if n.get('role')=='list' and any(c.get('name')=='Custom Stickers' for c in n.get('children') or [])]
        if not tiles or len(lists)!=1:raise ValueError('Native favorite layout needs refresh')
        return tiles,lists[0]['bounds']
    def scroll(area,direction):
        run_process(['xdotool','mousemove',str(round(area['x']+area['width']/2)),str(round(area['y']+area['height']/2)),
                        'click','--repeat','40','--delay','5',direction],check=True,timeout=3)
        time.sleep(.15)
    def clickable(node,viewport):
        if node is None:return False
        b=node['bounds']
        return min(b['y']+b['height'],viewport['y']+viewport['height'])-max(b['y'],viewport['y'])>=24
    try:
        tiles,area=panel(root)
    except ValueError:
        tabs=[n for n in walk(root) if n.get('role')=='page-tab' and n.get('name')=='Custom Stickers']
        if len(tabs)!=1:raise ValueError('Custom sticker tab is not unique')
        click_node(tabs[0]);time.sleep(.1);root=tree();tiles,area=panel(root)
    # Keep object-to-index anchors, never stale screen coordinates. A popup
    # rebuilt by Qt or a reordered favorite list invalidates the cache.
    cache_path=path.parent/'sticker-panel-anchors.cache'
    panel_ref=next((n.get('native_ref') for n in walk(root) if n.get('role')=='list'
                    and any(c.get('name')=='Custom Stickers' for c in n.get('children') or [])),None)
    known={}
    try:
        cached=json.loads(cache_path.read_text())
        if (panel_ref and cached.get('panel_ref')==panel_ref and cached.get('order')==order
                and 0<=time.time()-cached.get('updated',0)<60):
            known=cached.get('anchors') or {}
    except (OSError,ValueError):pass
    offsets={known[n['native_ref']]-i for i,n in enumerate(tiles) if n.get('native_ref') in known}
    shared=sum(n.get('native_ref') in known for n in tiles)
    offset=next(iter(offsets)) if len(offsets)==1 and shared>=2 else None
    relative=index-offset if offset is not None else -1
    chosen=tiles[relative] if 0<=relative<len(tiles) else None
    if clickable(chosen,area):
        job['favorite_cache']='anchor_hit'
    elif len(tiles)==len(order):
        known={n['native_ref']:i for i,n in enumerate(tiles) if n.get('native_ref')}
        # A full grid has an exact native index. Keep the current scroll position
        # when the desired tile is already visible instead of resetting it twice.
        chosen=tiles[index]
        if not clickable(chosen,area):
            b=chosen['bounds']
            scroll(area,'4' if b['y']<area['y'] else '5')
            root=tree();tiles,area=panel(root)
            chosen=tiles[index] if len(tiles)==len(order) else None
    else:
        tail_first=index>=len(order)-20 and index>=20
        scroll(area,'5' if tail_first else '4');root=tree();tiles,area=panel(root)
        initial_index=index-(len(order)-len(tiles)) if tail_first else index
        first_index=len(order)-len(tiles) if tail_first else 0
        known={n['native_ref']:first_index+i for i,n in enumerate(tiles) if n.get('native_ref')}
        chosen=tiles[initial_index] if 0<=initial_index<len(tiles) else None
        if chosen is None and not tail_first:
            # Qt virtualizes this grid and accelerates wheel events. Align the
            # stable accessible objects shared by consecutive pages instead of
            # inferring the row offset from pixel displacement.
            if any(not n.get('native_ref') for n in tiles):raise ValueError('Native favorite identities are unavailable')
            known={n['native_ref']:i for i,n in enumerate(tiles)}
            first_index=0
            job['favorite_scroll']=[]
            stagnant=0
            for _ in range(16):
                if time.monotonic()-started>12:break
                before_index=first_index
                before_refs=tuple(n.get('native_ref') for n in tiles)
                run_process(['xdotool','mousemove',str(round(area['x']+area['width']/2)),str(round(area['y']+area['height']/2)),
                    'click','--repeat','1','--delay','20','5'],check=True,timeout=3)
                time.sleep(.06);root=tree();tiles,area=panel(root)
                offsets={known[n['native_ref']]-i for i,n in enumerate(tiles) if n.get('native_ref') in known}
                aligned=len(offsets)==1 and all(n.get('native_ref') for n in tiles)
                if aligned:
                    first_index=offsets.pop()
                    for i,n in enumerate(tiles):known[n['native_ref']]=first_index+i
                job['favorite_scroll'].append([before_index,first_index,len(tiles)])
                relative=index-first_index
                chosen=tiles[relative] if aligned and 0<=relative<len(tiles) else None
                if clickable(chosen,area):break
                after_refs=tuple(n.get('native_ref') for n in tiles)
                stagnant=stagnant+1 if before_refs==after_refs and first_index==before_index else 0
                # Virtualized pages can briefly expose no shared anchor while
                # WeChat rebuilds its tile list. Continue one wheel notch at a
                # time; never infer an index from an ambiguous page.
                if stagnant>=3 or aligned and first_index==before_index and relative<0:break
        if not clickable(chosen,area):
            scroll(area,'5');root=tree();tiles,area=panel(root)
            tail_index=index-(len(order)-len(tiles))
            chosen=tiles[tail_index] if 0<=tail_index<len(tiles) else None
    if chosen is None:raise ValueError('Requested favorite is outside the currently exposed native page')
    # Qt normally exposes the full grid even when most rows are outside the viewport.
    # With a full grid the native order gives an exact index for middle favorites too.
    for _ in range(8):
        if time.monotonic()-started>12:break
        if len(tiles)!=len(order):break
        chosen=tiles[index];b=chosen['bounds']
        if clickable(chosen,area):break
        direction='4' if b['y']<area['y'] else '5'
        run_process(['xdotool','mousemove',str(round(area['x']+area['width']/2)),str(round(area['y']+area['height']/2)),
                        'click','--repeat','2','--delay','40',direction],check=True,timeout=3)
        time.sleep(.1);root=tree();tiles,area=panel(root)
    b=chosen['bounds']
    if not clickable(chosen,area):
        raise ValueError('Requested favorite has no clickable visible area')
    current=[r['md5'] for r in native_read('/stickers')['stickers']]
    if current!=order:raise ValueError('Favorite sync changed the order before sending')
    if not selected(root,name,is_group):raise ValueError('Destination changed before sticker send')
    if job.get('created') and time.time()-job['created']>=25:
        raise ValueError('表情定位超时，任务已作废，尚未点击发送')
    if panel_ref and known:
        try:
            fd=os.open(cache_path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            with os.fdopen(fd,'w') as stream:
                json.dump({'panel_ref':panel_ref,'order':order,'updated':time.time(),'anchors':known},stream)
        except OSError:pass
    job['favorite_seconds']=round(time.monotonic()-started,3)
    job['state']='sending';path.write_text(json.dumps(job,ensure_ascii=False))
    # A favorite tile sends immediately as WeChat type 47; never paste an image instead.
    visible_top=max(b['y'],area['y']);visible_bottom=min(b['y']+b['height'],area['y']+area['height'])
    run_process(['/opt/tools/click',str(round(b['x']+b['width']/2)),str(round((visible_top+visible_bottom)/2))],
                   check=True,stdout=subprocess.DEVNULL,timeout=3)
    job['state']='submitted'


def run_job(path, target_id, name, is_group, root=None):
    job=json.loads(path.read_text())
    if job.get('chat')!=target_id or not 0<=time.time()-job.get('created',0)<30:
        raise ValueError('UI job destination or age is invalid')
    if job.get('operation') not in ['send_text','send_image','send_sticker','collect_sticker','copy_image','read_image','open_chat','inspect_quote_menu']:
        raise ValueError('Unknown UI job operation')
    previews=preview_windows();close_previews(previews)
    if root is None or previews:root=tree()
    if not selected(root,name,is_group):raise ValueError('UI job chat selection changed')
    fd=os.open(str(path)+'.claimed',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
    if job['operation']=='inspect_quote_menu':
        try:
            menu, original = quote_context_menu(job, root)
            job['menu'] = [{k:n.get(k) for k in ('role','name','bounds')} for n in walk(menu)
                           if n.get('role') in ('menu','menu-item','check-menu-item')]
            job['state'] = 'done'
        except ValueError as exc:
            current = tree()
            job['quote_unavailable'] = str(exc)
            job['observed_rows'] = [n.get('name') for lst in walk(current)
                                    if lst.get('role') == 'list' and lst.get('name') == 'Messages'
                                    for n in lst.get('children') or [] if n.get('role') == 'list-item']
            job['state'] = 'done'
        finally:
            run_process(['xdotool','key','--clearmodifiers','Escape'],check=True,timeout=3)
    elif job['operation']=='collect_sticker':
        collect_native_sticker(job,path,root,name,is_group)
    elif job['operation']=='send_sticker':
        send_native_sticker(job,path,root,name,is_group)
    elif job['operation']=='send_image':
        from native_image_send import send_image
        send_image(job,path,root,name,is_group)
    elif job['operation']=='send_text':
        text=job.get('text')
        if not isinstance(text,str) or not text.strip() or len(text)>2000:raise ValueError('Invalid text input')
        job['quote_applied'] = False
        if job.get('quote_to'):
            try:
                root = prepare_quote(job, root)
            except (ValueError, OSError) as exc:
                # Optional quoting must not prevent an otherwise valid text reply.
                run_process(['xdotool','key','--clearmodifiers','Escape'],check=True,timeout=3)
                job['quote_fallback'] = str(exc)[:180]
                root = tree()
        editors=[n for n in walk(root) if n.get('role')=='text' and 'EDITABLE' in (n.get('states') or [])
                 and (n.get('bounds') or {}).get('height',0)>35 and (n.get('bounds') or {}).get('y',0)>200]
        if len(editors)!=1:raise ValueError('Composer was not uniquely identified')
        b=editors[0]['bounds']
        run_process(['/opt/tools/click',str(round(b['x']+b['width']/2)),str(round(b['y']+b['height']/2))],check=True,stdout=subprocess.DEVNULL,timeout=3)
        run_process(['xdotool','key','--clearmodifiers','ctrl+a'],check=True,timeout=3)
        if job.get('mentions'):
            from native_group_mentions import insert_mentions, clear_generated_mentions
            run_process(['xdotool','key','--clearmodifiers','BackSpace'],check=True,timeout=3)
            try:
                if not is_group:raise ValueError('私聊不发送群成员@提醒')
                job['mentions_applied'] = insert_mentions(job,editors[0])
            except (ValueError,OSError,subprocess.SubprocessError) as exc:
                clear_generated_mentions()
                job['mentions_applied'] = []
                job['mention_fallback'] = str(exc)[:180]
        run_process(['xclip','-selection','clipboard'],input=text.encode(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=3)
        run_process(['xdotool','key','--clearmodifiers','ctrl+v'],check=True,timeout=3)
        time.sleep(.1)
        if job.get('mentions_applied'):
            from native_group_mentions import mention_allowed, clear_generated_mentions
            if not mention_allowed(target_id):
                clear_generated_mentions()
                run_process(['xclip','-selection','clipboard'],input=text.encode(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=3)
                run_process(['xdotool','key','--clearmodifiers','ctrl+v'],check=True,timeout=3)
                job['mentions_applied'] = []
                job['mention_fallback'] = '发送前群聊@成员总开关已关闭'
        if not selected(tree(),name,is_group):raise ValueError('Destination changed before send')
        # Claim and persist the non-idempotent boundary before pressing Return once.
        job['state']='sending';path.write_text(json.dumps(job,ensure_ascii=False))
        run_process(['xdotool','key','--clearmodifiers','Return'],check=True,timeout=3)
        job['state']='submitted'
    elif job['operation'] in ('copy_image','read_image'):
        if job['operation']=='read_image':
            sys.path.insert(0,'/data/native-tools')
            from media_locator import locate_image
            raw=sorted(native_read('/messages',chat=target_id,limit=200)['messages'],key=lambda r:(r['timestamp'],r['localId']))
            lists=[n for n in walk(root) if n.get('role')=='list' and n.get('name')=='Messages']
            nodes=[n for n in lists[0].get('children',[]) if n.get('role')=='list-item' and
                   not re.fullmatch(r'\d{1,2}:\d{2}',normal(n.get('name')))] if len(lists)==1 else []
            server_id=str(job.get('server_id') or '')
            server_id=server_id if server_id.isdigit() else ''
            chosen=locate_image(raw,nodes,int(job['local_id']),3,server_id)
            if chosen is None:
                job.update(state='done',data='',quality='not_visible');path.write_text(json.dumps(job));return
            original=next(r for r in raw if r['localId']==int(job['local_id']) and (not server_id or str(r['serverId'])==server_id))
            b=chosen['bounds'];area=lists[0]['bounds']
            visible_top=max(b['y'],area['y'])
            visible_bottom=min(b['y']+b['height'],area['y']+area['height'])
            if visible_bottom-visible_top<100:
                job.update(state='done',data='',quality='partly_visible');path.write_text(json.dumps(job));return
            offset=min(140,b['width']/2)
            x=b['x']+b['width']-offset if original.get('isSelf') else b['x']+offset
            job.update(node_name=chosen.get('name'),bounds=b,xy=[x,(visible_top+visible_bottom)/2])
        nodes=[n for n in walk(root) if n.get('role')=='list-item' and n.get('name')==job.get('node_name') and n.get('bounds')==job.get('bounds')]
        if len(nodes)!=1:raise ValueError('Image position changed before copy')
        b=nodes[0]['bounds'];x,y=job['xy']
        if not b['x']<=x<=b['x']+b['width'] or not b['y']<=y<=b['y']+b['height']:raise ValueError('Image click outside message')
        run_process(['xclip','-selection','clipboard'],input=('image-job-'+path.stem).encode(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=3)
        run_process(['xdotool','mousemove',str(round(x)),str(round(y)),'click','--repeat','2','--delay','100','1'],check=True,timeout=3)
        opened=set()
        for _ in range(4):
            time.sleep(.15)
            opened=preview_windows()
            if opened:break
        data=b''
        if opened:
            try:
                run_process(['xdotool','key','--clearmodifiers','ctrl+c'],check=True,timeout=3)
                for _ in range(3):
                    time.sleep(.15)
                    p=run_process(['xclip','-selection','clipboard','-t','image/png','-o'],capture_output=True,timeout=2)
                    if p.returncode==0 and p.stdout.startswith(b'\x89PNG\r\n\x1a\n') and len(p.stdout)<=10*1024*1024:
                        data=p.stdout;break
            finally:
                close_previews(opened)
        job['state']='done';job['data']=base64.b64encode(data).decode();job['format']='png';job['quality']='native_clipboard' if data else 'clipboard_unavailable'
    else:
        direction=job.get('scroll')
        if direction:
            if direction not in ('up','down','latest'):raise ValueError('Unknown history scroll direction')
            lists=[n for n in walk(root) if n.get('role')=='list' and n.get('name')=='Messages']
            if len(lists)!=1:raise ValueError('Message list is not unique')
            b=lists[0]['bounds']
            run_process(['xdotool','mousemove',str(round(b['x']+b['width']/2)),str(round(b['y']+b['height']/2)),
                            'click','--repeat','40' if direction=='latest' else '4','--delay','5' if direction=='latest' else '65','4' if direction=='up' else '5'],check=True,timeout=3)
            time.sleep(.15)
        job['state']='done'
    path.write_text(json.dumps(job,ensure_ascii=False))


def visible_chat(root, name):
    lists = [n for n in walk(root) if n.get('role') == 'list' and n.get('name') == 'Chats']
    if len(lists) != 1:
        raise ValueError('Native chat list not uniquely identified')
    matches = [n for n in lists[0].get('children') or [] if n.get('role') == 'list-item'
               and (normal(n.get('name')) == name or normal(n.get('name')).startswith(name + ' '))]
    if len(matches) > 1:
        raise ValueError('Target chat is not uniquely visible in the native chat list')
    return lists[0], matches[0] if matches else None


def locate_chat(root, name):
    """Only scroll the chat list, keeping the normal visible-target path immediate."""
    listing, candidate = visible_chat(root, name)
    if candidate:
        return candidate
    bounds = listing.get('bounds') or {}
    if not bounds.get('width') or not bounds.get('height'):
        raise ValueError('Native chat list has no usable bounds')
    x, y = round(bounds['x'] + bounds['width'] / 2), round(bounds['y'] + bounds['height'] / 2)
    run_process(['xdotool', 'mousemove', str(x), str(y), 'click', '--repeat', '80', '--delay', '1', '4'],
                check=True, timeout=3)
    previous = None
    for _ in range(18):
        time.sleep(.1)
        listing, candidate = visible_chat(tree(), name)
        if candidate:
            return candidate
        signature = tuple(normal(n.get('name')) for n in listing.get('children') or [] if n.get('role') == 'list-item')
        if signature == previous:
            break
        previous = signature
        run_process(['xdotool', 'mousemove', str(x), str(y), 'click', '--repeat', '4', '--delay', '20', '5'],
                    check=True, timeout=3)
    raise ValueError('遍历微信会话列表后仍未找到目标会话，尚未发送')


def select_chat():
    global SKIP_MESSAGES
    arguments = sys.argv[1:]
    positional = []
    index = 0
    while index < len(arguments):
        if arguments[index] == "--force":
            index += 1
        elif arguments[index] == "--click-xy":
            index += 3
        else:
            positional.append(arguments[index])
            index += 1
    if len(positional) != 1 or positional[0].startswith("--"):
        raise ValueError("Expected one exact chat ID")
    target_id = positional[0]
    job_path=None
    if '::whalejob::' in target_id:
        target_id,nonce=target_id.split('::whalejob::',1)
        if not re.fullmatch('[0-9a-f]{32}',nonce):raise ValueError('Invalid UI job identity')
        job_path=Path('/data/whalegirl-jobs')/(nonce+'.json')
        job_spec=json.loads(job_path.read_text())
        SKIP_MESSAGES = (str(job_spec.get('operation','')).startswith('moments_') or job_spec.get('operation') in ('send_sticker','send_image','open_chat') or
                         job_spec.get('operation')=='send_text' and not job_spec.get('quote_to'))
        if str(job_spec.get('operation', '')).startswith('moments_'):
            try:
                from moments_ui import run
                run(job_path, target_id)
            except Exception as exc:
                job=json.loads(job_path.read_text())
                if not job.get('state'):job['state']='failed_before_send'
                job['error']=str(exc)[:200];job_path.write_text(json.dumps(job,ensure_ascii=False))
                raise
            return {'ok':True,'username':target_id,'skipped':True}
    def finish(skipped):
        if job_path:
            try:
                run_job(job_path,target_id,name,is_group,root)
                completed=json.loads(job_path.read_text())
                completed['ui_timing']={k:round(v,3) if isinstance(v,float) else v for k,v in PROFILE.items()}
                completed['ui_timing']['compact_snapshot']=SKIP_MESSAGES
                job_path.write_text(json.dumps(completed,ensure_ascii=False))
            except Exception as exc:
                job=json.loads(job_path.read_text())
                if not job.get('state'):job['state']='failed_before_send'
                job['error']=str(exc)[:200];job_path.write_text(json.dumps(job,ensure_ascii=False))
                raise
        return {'ok':True,'username':target_id,'skipped':skipped}
    rows = chats()
    targets = [c for c in rows if c.get("id") == target_id]
    if len(targets) != 1:
        raise ValueError("Target chat ID is not unique")
    is_group = bool(targets[0].get("isGroup"))
    name = normal(targets[0].get("remark") or targets[0].get("name"))
    if not name or sum(normal(c.get("remark") or c.get("name")) == name for c in rows) != 1:
        raise ValueError("Chat display name is not unique")
    root = tree()
    moments_frames=[n for n in walk(root) if n.get('role')=='frame' and n.get('name')=='Moments']
    if moments_frames:
        from moments_ui import editors, button
        if len(moments_frames)!=1 or editors(moments_frames[0]):
            raise ValueError('Moments editor is still open; do not type into the wrong window')
        click_node(button(moments_frames[0],'Close'))
        time.sleep(.12)
        root=tree()
    if selected(root, name, is_group):
        return finish(True)
    b = locate_chat(root, name).get("bounds") or {}
    if not b.get("width") or not b.get("height"):
        raise ValueError("Target chat has no usable bounds")
    run_process(["/opt/tools/click", str(round(b["x"] + b["width"] / 2)), str(round(b["y"] + b["height"] / 2))],
                   capture_output=True, check=True, timeout=8)
    for _ in range(10):
        root=tree()
        if selected(root, name, is_group):
            return finish(False)
        time.sleep(.15)
    raise ValueError("Native chat selection was not confirmed by both selected row and header")


def main():
    try:
        return select_chat()
    except Exception as exc:
        # Selection can fail before run_job() is called. Persist that failure too,
        # rather than leaking the upstream plan's generic "No action selected".
        match = re.fullmatch(r'(.+)::whalejob::([0-9a-f]{32})', sys.argv[-1] if len(sys.argv) > 1 else '')
        if match:
            path = Path('/data/whalegirl-jobs') / (match[2] + '.json')
            try:
                job = json.loads(path.read_text())
                if job.get('chat') == match[1]:
                    if not job.get('state'):
                        job['state'] = 'failed_before_send'
                    job['error'] = str(exc)[:200]
                    path.write_text(json.dumps(job, ensure_ascii=False))
            except (OSError, ValueError):
                pass
        raise


if __name__ == "__main__":
    try:
        result = main()
    except Exception as exc:
        result = {"ok": False, "error": str(exc)[:240]}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["ok"] else 1)
