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


def tree(sticker_panel_ref=None, *, chat_root=None, chat_name='', is_group=False, require_focus=False):
    started = time.monotonic()
    deadline=started+8
    for attempt in range(3):
        try:
            if sticker_panel_ref:
                # Reuse only the connection, never old objects or coordinates.
                from native_panel_reader import read
                value=read(sticker_panel_ref)
            elif SKIP_MESSAGES:
                try:
                    from native_send_reader import read
                    if chat_root is not None:
                        try:
                            value=read(chat_root,name=chat_name,is_group=is_group,require_focus=require_focus)
                            PROFILE['control_snapshots']=PROFILE.get('control_snapshots',0)+1
                        except (ValueError,OSError):
                            PROFILE['control_fallbacks']=PROFILE.get('control_fallbacks',0)+1
                            value=read()
                    else:
                        value=read()
                except (ImportError,FileNotFoundError):
                    result=run_process(['/opt/tools/a11y-dump','--format','json','--skip-messages'],
                        capture_output=True,text=True,check=True,timeout=max(.1,deadline-time.monotonic()))
                    value=json.loads(result.stdout)
            else:
                command = ["/opt/tools/a11y-dump", "--format", "json"]
                if SKIP_MESSAGES:
                    command.append('--skip-messages')
                result = run_process(command,capture_output=True,text=True,check=True,
                                     timeout=max(.1,deadline-time.monotonic()))
                value=json.loads(result.stdout)
            break
        except (TimeoutError,subprocess.CalledProcessError) as exc:
            if isinstance(exc,TimeoutError):
                busy=str(exc)=='Accessibility reader busy'
            else:
                try:
                    failure=json.loads(exc.stdout)
                    busy=isinstance(failure,dict) and failure.get('error')=='TimeoutError'
                except (ValueError,TypeError):busy=False
            # A busy reader expires its lock wait after 1.2 s. A timed-out
            # full traversal can take the entire deadline and is not replayed.
            # Repeat only this read, never the picker click or a send action.
            if not busy or attempt==2 or time.monotonic()-started>=3.5:
                raise
            PROFILE['snapshot_contention_retries']=PROFILE.get('snapshot_contention_retries',0)+1
            time.sleep(.1)
    elapsed=time.monotonic()-started
    PROFILE['snapshots'] += 1
    PROFILE['snapshot_seconds'] += elapsed
    if sticker_panel_ref:
        PROFILE['panel_snapshots']=PROFILE.get('panel_snapshots',0)+1
        PROFILE['panel_snapshot_seconds']=PROFILE.get('panel_snapshot_seconds',0)+elapsed
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
    matching_row = any(re.match(re.escape(display_name) + r'(?=\s|$)', normal(n.get("name"))) is not None for n in chosen)
    header = any(n.get("role") == "label" and header_matches(n.get("name"), display_name, is_group)
                 and (n.get("bounds") or {}).get("x", -1) > right
                 and (n.get("bounds") or {}).get("y", 1000) < bounds.get("y", 100) for n in nodes)
    return matching_row and header


def window_signature():
    result=run_process(['xdotool','getactivewindow','getwindowgeometry','--shell'],
                       capture_output=True,text=True,check=True,timeout=2)
    return result.stdout.strip()


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


def native_read(route, *, _timeout=6, **params):
    jobs = Path('/data/whalegirl-jobs')
    endpoint = (jobs / 'native-read.endpoint').read_text().strip()
    if not re.fullmatch(r'native-read-\d+\.sock', endpoint):
        raise ValueError('Native read endpoint is invalid')
    connection = http.client.HTTPConnection('localhost', timeout=_timeout)
    connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.sock.settimeout(_timeout)
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


def ensure_favorite_panel(root,name,is_group):
    """Open favorites through a unique control or the documented hotkey."""
    tabs=[n for n in walk(root) if n.get('role')=='page-tab' and n.get('name')=='Custom Stickers']
    if tabs:return root
    buttons=[n for n in walk(root) if n.get('role')=='push-button'
             and normal(n.get('name')).casefold()=='send sticker(alt+e)']
    if len(buttons)==1:
        click_node(buttons[0])
    else:
        # Compact and transitional AT-SPI trees can omit or duplicate the
        # toolbar node. The named Alt+E action only opens the picker; it never
        # chooses a tile or sends a message.
        run_process(['xdotool','key','--clearmodifiers','alt+e'],check=True,timeout=3)
    time.sleep(.06)
    root=tree()
    if not selected(root,name,is_group):
        raise ValueError('Destination changed while opening the favorite panel')
    if not any(n.get('role')=='page-tab' and n.get('name')=='Custom Stickers' for n in walk(root)):
        raise ValueError('Native favorite panel did not open')
    return root


def favorite_tile(tiles, index, total, known):
    """A full Qt grid can become virtualized immediately after scrolling."""
    if len(tiles)==total:
        known.update({n['native_ref']:i for i,n in enumerate(tiles) if n.get('native_ref')})
        return tiles[index] if 0<=index<len(tiles) else None
    offsets={known[n['native_ref']]-i for i,n in enumerate(tiles) if n.get('native_ref') in known}
    shared=sum(n.get('native_ref') in known for n in tiles)
    if len(offsets)!=1 or shared<2:return None
    first=next(iter(offsets));relative=index-first
    known.update({n['native_ref']:first+i for i,n in enumerate(tiles) if n.get('native_ref')})
    return tiles[relative] if 0<=relative<len(tiles) else None


def favorite_page_signature(tiles):
    # Qt can keep the same accessible objects while their visible positions move.
    return tuple((node.get('native_ref'), *(
        (node.get('bounds') or {}).get(key) for key in ('x','y','width','height')))
        for node in tiles)


GRID_TOLERANCE=4


def _grid_centers(values,tolerance):
    centers=[]
    for value in values:
        if not any(abs(value-center)<=tolerance for center in centers):centers.append(value)
    return sorted(centers)


def favorite_grid(tiles,first,columns=None):
    """Return the row-major layout implied by `first`, or None if geometry disagrees.

    Qt recycles accessible tile objects while the virtual favorite grid
    scrolls, so a native_ref alone can name a different favorite after a wheel
    event. Geometry cannot be recycled: favorite i always sits in column
    (i+shift) % cols, and only a column wrap starts the next row. A page whose
    inferred first index disagrees with that layout must never be clicked.
    """
    if first<0 or not tiles:return None
    bounds=[node.get('bounds') or {} for node in tiles]
    if any(not all(key in b for key in ('x','y','width','height')) for b in bounds):return None
    tolerance=max(GRID_TOLERANCE,min(b['width'] for b in bounds)/3)
    xs=list(columns) if columns else _grid_centers([b['x'] for b in bounds],tolerance)
    cols=len(xs)
    if not cols:return None
    shifts=set()
    for offset,b in enumerate(bounds):
        column=min(range(cols),key=lambda c:abs(xs[c]-b['x']))
        if abs(xs[column]-b['x'])>tolerance:return None
        shifts.add((column-(first+offset))%cols)
    if len(shifts)!=1:return None
    shift=shifts.pop()
    height=min(b['height'] for b in bounds)
    for offset in range(1,len(bounds)):
        same_row=(first+offset+shift)//cols==(first+offset-1+shift)//cols
        step=bounds[offset]['y']-bounds[offset-1]['y']
        if same_row and abs(step)>tolerance:return None
        if not same_row and not tolerance<step<=2*height+tolerance:return None
    return {'xs':xs,'shift':shift}


def favorite_grid_matches(tiles,first,total,grid):
    """True only when `first` places every visible tile in its calibrated cell."""
    if not grid or first<0 or first+len(tiles)>total:return False
    found=favorite_grid(tiles,first,grid['xs'])
    return bool(found) and found['shift']==grid['shift']


def favorite_page_offset(tiles,known,total,grid):
    """Infer a virtual page from a strong anchor quorum plus calibrated geometry.

    WeChat recycles a few AT-SPI tile objects when Qt scrolls. Their old native
    refs can disagree with the rest of the settled page even though the visible
    row-major geometry is stable. Ignore conflicting refs only when one offset
    has an unambiguous 70% quorum (at least five refs) and the whole page still
    matches the previously calibrated grid. Otherwise refuse to select.
    """
    positions={}
    duplicated=set()
    for position,node in enumerate(tiles):
        ref=node.get('native_ref')
        if not ref:continue
        if ref in positions:duplicated.add(ref)
        else:positions[ref]=position
    offsets={}
    for ref,position in positions.items():
        if ref not in duplicated and ref in known:
            offset=known[ref]-position
            offsets[offset]=offsets.get(offset,0)+1
    if not offsets:return None
    candidates=sorted(((count,offset) for offset,count in offsets.items()
                       if favorite_grid_matches(tiles,offset,total,grid)),reverse=True)
    if not candidates:return None
    support,first=candidates[0]
    shared=sum(offsets.values())
    required=max(5,(shared*7+9)//10)
    if support<required or len(candidates)>1 and candidates[1][0]==support:return None
    return first


def rebind_favorite_panel(root,name,is_group):
    """Accept a rebuilt panel only after rechecking the selected destination."""
    if not selected(root,name,is_group):
        raise ValueError('Destination changed while refreshing the favorite panel')
    lists=[n for n in walk(root) if n.get('role')=='list'
           and any(c.get('name')=='Custom Stickers' for c in n.get('children') or [])]
    if len(lists)!=1 or not lists[0].get('native_ref'):
        raise ValueError('Rebuilt native favorite panel is not unique')
    return lists[0].get('native_ref')


def sticker_local_baseline(chat):
    rows=native_read('/messages',chat=chat,limit=8,_timeout=2).get('messages',[])
    return max((int(m.get('localId') or 0) for m in rows),default=0)


def await_sticker_local(chat,md5,before):
    """Give Qt time to enqueue a cold sticker before dismissing its popup.

    This is local creation, not server acknowledgement. A missing row never
    authorizes another click; durable acknowledgement stays in delivery.py.
    """
    deadline=time.monotonic()+1.8
    for delay in (.06,.1,.16,.24,.32):
        time.sleep(delay)
        remaining=deadline-time.monotonic()
        if remaining<=0:break
        try:rows=native_read('/messages',chat=chat,limit=8,_timeout=min(.35,remaining)).get('messages',[])
        except (ValueError,OSError,TimeoutError):continue
        matches=[m for m in rows if m.get('isSelf') is True and m.get('type')==47
                 and int(m.get('localId') or 0)>before and (m.get('sticker') or {}).get('md5')==md5]
        if len(matches)==1:return int(matches[0]['localId'])
    return 0


def _send_native_sticker(job,path,root,name,is_group):
    started=time.monotonic()
    md5=job.get('md5','')
    if not re.fullmatch(r'[0-9a-f]{32}',md5):raise ValueError('Invalid sticker identity')
    order=[r['md5'] for r in native_read('/stickers')['stickers']]
    if order.count(md5)!=1:raise ValueError('Requested sticker is not in native favorites')
    root=ensure_favorite_panel(root,name,is_group)
    index=order.index(md5);total=len(order)
    PROFILE.update(favorite_total=total,favorite_target=index,favorite_locator_version=2)
    def panel(root):
        tiles=sorted([n for n in walk(root) if n.get('role')=='label' and n.get('name')=='Custom Stickers'
                      and (n.get('bounds') or {}).get('width',0)>50],key=lambda n:(n['bounds']['y'],n['bounds']['x']))
        lists=[n for n in walk(root) if n.get('role')=='list' and any(c.get('name')=='Custom Stickers' for c in n.get('children') or [])]
        if not tiles or len(lists)!=1:raise ValueError('Native favorite layout needs refresh')
        return tiles,lists[0]['bounds']
    def wheel(area,direction,repeat,delay,counter):
        PROFILE[counter]=PROFILE.get(counter,0)+1
        run_process(['xdotool','mousemove',str(round(area['x']+area['width']/2)),str(round(area['y']+area['height']/2)),
                        'click','--repeat',str(repeat),'--delay',str(delay),direction],check=True,timeout=3)
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
    # rebuilt by Qt or a reordered favorite list invalidates the cache. Anchors
    # are only reused together with the grid calibration that verified them.
    cache_path=path.parent/'sticker-panel-anchors.cache'
    panel_ref=next((n.get('native_ref') for n in walk(root) if n.get('role')=='list'
                    and any(c.get('name')=='Custom Stickers' for c in n.get('children') or [])),None)
    known={};grid=None
    try:
        cached=json.loads(cache_path.read_text())
        if (cached.get('schema')==2 and job.get('fast_session_nonce')
                and cached.get('session')==job['fast_session_nonce']
                and cached.get('page')==[list(row) for row in favorite_page_signature(tiles)]
                and panel_ref and not cached.get('invalidated') and cached.get('panel_ref')==panel_ref
                and cached.get('order')==order
                and 0<=time.time()-cached.get('updated',0)<1800 and cached.get('grid')):
            known=dict(cached.get('anchors') or {});grid=cached['grid']
    except (OSError,ValueError,TypeError,AttributeError):pass
    partial_snapshot=False
    trusted_page=None
    def refresh_panel():
        nonlocal partial_snapshot,panel_ref
        # Only wheel events occur between these reads. The final click still
        # requires a fresh full tree and destination check below.
        if panel_ref and re.fullmatch(r':\d+\.\d+\|/org/a11y/atspi/accessible/[A-Za-z0-9_/]+',panel_ref):
            partial_snapshot=True
            try:
                return tree(sticker_panel_ref=panel_ref)
            except ValueError as exc:
                if 'Native sticker panel reference is stale' not in str(exc):
                    raise
                rebuilt=tree()
                next_ref=rebind_favorite_panel(rebuilt,name,is_group)
                if next_ref!=panel_ref:
                    panel_ref=next_ref
                    known.clear()
                    PROFILE['panel_rebinds']=PROFILE.get('panel_rebinds',0)+1
                return rebuilt
        return tree()
    def settle_panel(reads=8):
        """Read until two consecutive snapshots agree.

        Qt animates every wheel notch for roughly 0.15-0.3 s (live probe
        2026-11-09: one notch moves ~37 px of an 88 px row). A read taken
        mid-animation shows tiles moving by different amounts, which the rigid
        and grid checks correctly refuse; the walk then lost its anchors and
        mid-list favorites failed. Only settled pages are used for alignment.
        """
        nonlocal root,tiles,area
        time.sleep(.05)
        root=refresh_panel();tiles,area=panel(root)
        for _ in range(reads):
            time.sleep(.035)
            again=refresh_panel();again_tiles,again_area=panel(again)
            same=favorite_page_signature(again_tiles)==favorite_page_signature(tiles)
            root,tiles,area=again,again_tiles,again_area
            if same:return True
        PROFILE['settle_timeout']=PROFILE.get('settle_timeout',0)+1
        return False
    def reject():
        PROFILE['grid_rejected']=PROFILE.get('grid_rejected',0)+1
    def rigid(tiles,previous):
        # A wheel moves the whole grid together. Qt may recycle a minority of
        # refs during that move, so require a strong, unique movement quorum.
        if not previous:return True
        old={};old_duplicates=set()
        for node in previous:
            ref=node.get('native_ref')
            if not ref:continue
            if ref in old:old_duplicates.add(ref)
            else:old[ref]=(node.get('bounds') or {}).get('y')
        new={};new_duplicates=set()
        for node in tiles:
            ref=node.get('native_ref')
            if not ref:continue
            if ref in new:new_duplicates.add(ref)
            else:new[ref]=(node.get('bounds') or {}).get('y')
        moves=[]
        for ref,before_y in old.items():
            after_y=new.get(ref)
            if ref not in old_duplicates and ref not in new_duplicates and before_y is not None and after_y is not None:
                moves.append(after_y-before_y)
        if not moves:return True
        counts={}
        for move in moves:
            counts[move]=counts.get(move,0)+1
        ranked=sorted(counts.values(),reverse=True)
        required=max(5,(len(moves)*7+9)//10)
        return ranked[0]>=required and (len(ranked)==1 or ranked[0]>ranked[1])
    def page_first(tiles,previous=None):
        """Absolute index of the first tile, only when anchors and geometry agree."""
        first=favorite_page_offset(tiles,known,total,grid)
        if first is None or len(tiles)!=total and not rigid(tiles,previous):
            reject();return None
        # Once the complete row-major page is independently verified, refresh
        # its anchors from geometry. Duplicate refs within this one snapshot
        # remain unusable because they cannot identify a single favorite.
        refs=[n.get('native_ref') for n in tiles]
        next_known={}
        for ref in set(ref for ref in refs if ref):
            positions=[i for i,value in enumerate(refs) if value==ref]
            if len(positions)==1:next_known[ref]=first+positions[0]
        # Qt recycles delegates that just left the viewport. They cannot stay
        # in an absolute-index map: on the next scroll they name new stickers.
        known.clear();known.update(next_known)
        return first
    def locate(tiles,previous=None):
        """The target tile only when its absolute position is proven for this page."""
        if len(tiles)==total:
            known.clear();known.update({n['native_ref']:i for i,n in enumerate(tiles) if n.get('native_ref')})
            return tiles[index]
        if grid is None:
            # Without a calibrated grid only the confirmed boundary page itself is exact.
            if trusted_page is None or favorite_page_signature(tiles)!=trusted_page:return None
            offsets={known[n['native_ref']]-i for i,n in enumerate(tiles) if n.get('native_ref') in known}
            first=next(iter(offsets)) if len(offsets)==1 else None
        else:first=page_first(tiles,previous)
        relative=index-first if first is not None else -1
        return tiles[relative] if 0<=relative<len(tiles) else None
    def boundary(direction):
        """Scroll to an extreme, wait until Qt stops moving, and prove it is the extreme."""
        nonlocal root,tiles,area,trusted_page
        wheel(area,direction,40,5,'boundary_scroll');time.sleep(.07)
        root=refresh_panel();tiles,area=panel(root)
        for _ in range(5):
            time.sleep(.035)
            again=refresh_panel();again_tiles,again_area=panel(again)
            settled=favorite_page_signature(again_tiles)==favorite_page_signature(tiles)
            root,tiles,area=again,again_tiles,again_area
            if settled:break
        else:
            raise ValueError('Native favorite boundary did not settle')
        # A real extreme cannot move further in the same direction. A page that
        # still moves was read mid-animation or short of the end of the list.
        for edge_attempt in range(2):
            wheel(area,direction,3,5,'boundary_check');time.sleep(.04)
            again=refresh_panel();again_tiles,again_area=panel(again)
            if favorite_page_signature(again_tiles)==favorite_page_signature(tiles):
                root,tiles,area=again,again_tiles,again_area
                break
            # The first long scroll may stop one row short while Qt is finishing
            # its animation. Re-anchor and prove the edge again before selection.
            root,tiles,area=again,again_tiles,again_area
            if edge_attempt==1:
                raise ValueError('Native favorite boundary did not settle')
            wheel(area,direction,40,5,'boundary_scroll');time.sleep(.07)
            settled=False
            for _ in range(5):
                settle_root=refresh_panel();settle_tiles,settle_area=panel(settle_root)
                if favorite_page_signature(settle_tiles)==favorite_page_signature(tiles):
                    root,tiles,area=settle_root,settle_tiles,settle_area
                    settled=True
                    break
                root,tiles,area=settle_root,settle_tiles,settle_area
                time.sleep(.035)
            if not settled:
                raise ValueError('Native favorite boundary did not settle')
        first=0 if direction=='4' else total-len(tiles)
        if first<0:raise ValueError('Native favorite layout needs refresh')
        trusted_page=favorite_page_signature(tiles)
        known.clear();known.update({n['native_ref']:first+i for i,n in enumerate(tiles) if n.get('native_ref')})
        return first
    def calibrate(first,previous):
        nonlocal grid
        found=favorite_grid(tiles,first)
        if found is None or previous is not None and (
                len(previous['xs'])!=len(found['xs']) or previous['shift']!=found['shift']):
            grid=None;job['favorite_grid']='unverified'
        else:
            grid=found;job['favorite_grid']={'cols':len(found['xs']),'shift':found['shift']}
        return grid
    chosen=None
    first_index=page_first(tiles) if known else None
    if first_index is not None:
        relative=index-first_index
        chosen=tiles[relative] if 0<=relative<len(tiles) else None
        if not clickable(chosen,area) and len(tiles)<total:
            # A verified page already has absolute object indexes. Move from that
            # page instead of first resetting to an extreme and walking back.
            # Every move is still aligned against shared live objects before use.
            for _ in range(6):
                if time.monotonic()-started>10:break
                direction=('4' if chosen['bounds']['y']<area['y'] else '5') if chosen is not None else (
                    '4' if index<first_index else '5')
                before_page=favorite_page_signature(tiles);previous=tiles
                wheel(area,direction,2,12,'anchor_scroll')
                settle_panel()
                chosen=locate(tiles,previous)
                if clickable(chosen,area):
                    job['favorite_cache']='anchor_scroll'
                    break
                first_index=page_first(tiles,previous)
                if first_index is None:break
                if before_page==favorite_page_signature(tiles):break
    if clickable(chosen,area):
        job.setdefault('favorite_cache','anchor_hit')
    elif len(tiles)==total:
        known.clear();known.update({n['native_ref']:i for i,n in enumerate(tiles) if n.get('native_ref')})
        # A full grid has an exact native index. Keep the current scroll position
        # when the desired tile is already visible instead of resetting it twice.
        chosen=tiles[index]
        if not clickable(chosen,area):
            b=chosen['bounds']
            wheel(area,'4' if b['y']<area['y'] else '5',40,5,'boundary_scroll')
            settle_panel()
            chosen=locate(tiles)
    else:
        chosen=None
        direction='4' if index<total/2 else '5'
        first_index=boundary(direction);calibrate(first_index,None)
        relative=index-first_index
        chosen=tiles[relative] if 0<=relative<len(tiles) else None
        job['favorite_scroll']=[]
        if clickable(chosen,area):job['favorite_cache']='top_page' if direction=='4' else 'bottom_page'
        else:
            chosen=None
            if grid is None:raise ValueError('Native favorite grid could not be verified')
            stagnant=0
            for _ in range(48):
                if time.monotonic()-started>18:break
                before_index=first_index
                before_page=favorite_page_signature(tiles);previous=tiles
                # Every move overlaps the immediately preceding proven page.
                # Never scroll onward from an unproven page, losing its anchors.
                position=index-first_index
                target_node=tiles[position] if 0<=position<len(tiles) else None
                direction=('4' if target_node['bounds']['y']<area['y'] else '5') if target_node else ('4' if index<first_index else '5')
                far=index<first_index-2*len(grid['xs']) or index>=first_index+len(tiles)+2*len(grid['xs'])
                visible_rows=max(1,(len(tiles)+len(grid['xs'])-1)//len(grid['xs']))
                repeat=min(3,max(1,visible_rows-2)) if far else 1
                wheel(area,direction,repeat,12,'alignment_scroll')
                settle_panel()
                found=page_first(tiles,previous)
                if found is None:
                    settle_panel()
                    found=page_first(tiles,previous)
                if found is None:raise ValueError('Native favorite position became ambiguous before sending')
                first_index=found
                job['favorite_scroll'].append([before_index,first_index,len(tiles)])
                relative=index-first_index
                chosen=tiles[relative] if 0<=relative<len(tiles) else None
                if clickable(chosen,area):break
                chosen=None
                stagnant=stagnant+1 if before_page==favorite_page_signature(tiles) else 0
                if stagnant>=2:break
    if chosen is None:raise ValueError('Requested favorite is outside the currently exposed native page')
    # Keep the exact target in view; every page reached by a wheel is re-proven.
    previous=None
    for _ in range(8):
        if time.monotonic()-started>12:break
        chosen=locate(tiles,previous)
        if chosen is None:break
        b=chosen['bounds']
        if clickable(chosen,area):break
        direction='4' if b['y']<area['y'] else '5'
        previous=tiles
        wheel(area,direction,2,12,'visibility_scroll')
        settle_panel()
    if chosen is None:raise ValueError('Native favorite position became ambiguous before sending')
    if partial_snapshot:
        tiles_before_full=tiles
        root=tree();tiles,area=panel(root)
        current_panel=next((n.get('native_ref') for n in walk(root) if n.get('role')=='list'
            and any(c.get('name')=='Custom Stickers' for c in n.get('children') or [])),None)
        if current_panel!=panel_ref:raise ValueError('Native favorite panel changed before sending')
        before_full=tiles_before_full
        chosen=locate(tiles,before_full)
        if chosen is None:raise ValueError('Native favorite identity changed before sending')
    b=chosen['bounds']
    if not clickable(chosen,area):
        raise ValueError('Requested favorite has no clickable visible area')
    before=sticker_local_baseline(job['chat'])
    # Inventory may sync while the local message baseline is being read.
    # Verify the complete ordered MD5 list after that read, just before click.
    current=[r['md5'] for r in native_read('/stickers')['stickers']]
    if current!=order:raise ValueError('Favorite sync changed the order before sending')
    if not selected(root,name,is_group):raise ValueError('Destination changed before sticker send')
    if job.get('created') and time.time()-job['created']>=25:
        raise ValueError('表情定位超时，任务已作废，尚未点击发送')
    if panel_ref and known:
        try:
            fd=os.open(cache_path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            with os.fdopen(fd,'w') as stream:
                json.dump({'schema':2,'session':job.get('fast_session_nonce',''),
                    'page':[list(row) for row in favorite_page_signature(tiles)],
                    'panel_ref':panel_ref,'order':order,'updated':time.time(),'anchors':known,'grid':grid},stream)
        except OSError:pass
    if job.get('fast_session_nonce'):
        from native_fast_turn_v1 import check_guard
        check_guard(job,path)
    if job.get('created') and time.time()-job['created']>=25:
        raise ValueError('表情定位超时，任务已作废，尚未点击发送')
    job['favorite_seconds']=round(time.monotonic()-started,3)
    job['state']='sending';path.write_text(json.dumps(job,ensure_ascii=False))
    # A favorite tile sends immediately as WeChat type 47; never paste an image instead.
    visible_top=max(b['y'],area['y']);visible_bottom=min(b['y']+b['height'],area['y']+area['height'])
    run_process(['/opt/tools/click',str(round(b['x']+b['width']/2)),str(round((visible_top+visible_bottom)/2))],
                   check=True,stdout=subprocess.DEVNULL,timeout=3)
    job['state']='submitted'
    local_id=await_sticker_local(job['chat'],md5,before)
    job['sticker_local_id']=local_id
    PROFILE['sticker_local_row_seen']=bool(local_id)


def _detect_left_avatar(width, height, rows, dx_min=16, dx_max=62):
    """Detect the left-gutter avatar box inside a row PNG without PIL."""
    from collections import Counter

    dx_end = min(width, dx_max + 1)
    if width <= dx_min or dx_end - dx_min < 18 or height < 22 or len(rows) != height:
        return None
    gutter_cols = max(1, min(10, dx_min, width))
    bg_samples = []
    for row in rows:
        for x in range(gutter_cols):
            idx = x * 3
            bg_samples.append((row[idx], row[idx + 1], row[idx + 2]))
    if not bg_samples:
        return None
    bg = Counter(bg_samples).most_common(1)[0][0]
    active_rows = []
    for y, row in enumerate(rows):
        hits = 0
        for x in range(dx_min, dx_end):
            idx = x * 3
            if max(abs(row[idx] - bg[0]), abs(row[idx + 1] - bg[1]), abs(row[idx + 2] - bg[2])) > 12:
                hits += 1
        if hits >= 8:
            active_rows.append(y)
    if not active_rows:
        return None
    bands = []
    start = prev = active_rows[0]
    for y in active_rows[1:]:
        if y - prev <= 3:
            prev = y
        else:
            bands.append((start, prev))
            start = prev = y
    bands.append((start, prev))
    y0, y1 = max(bands, key=lambda item: item[1] - item[0])
    avatar_h = y1 - y0 + 1
    if avatar_h < 22:
        return None
    col_threshold = max(6, avatar_h // 3)
    active_cols = []
    for x in range(dx_min, dx_end):
        idx = x * 3
        hits = sum(
            1 for y in range(y0, y1 + 1)
            if max(abs(rows[y][idx] - bg[0]), abs(rows[y][idx + 1] - bg[1]), abs(rows[y][idx + 2] - bg[2])) > 12
        )
        if hits >= col_threshold:
            active_cols.append(x)
    if not active_cols:
        return None
    x0, x1 = active_cols[0], active_cols[-1]
    avatar_w = x1 - x0 + 1
    if avatar_w < 18:
        return None
    return (round((x0 + x1) / 2), round((y0 + y1) / 2),
            {'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1, 'width': avatar_w, 'height': avatar_h})


def _locate_pat_row(raw, root, pat_target):
    """Find the most recent visible incoming bubble node belonging to pat_target."""
    from difflib import SequenceMatcher

    lists = [n for n in walk(root) if n.get('role') == 'list' and n.get('name') == 'Messages']
    if len(lists) != 1:
        return None, None, None
    area = lists[0].get('bounds') or {}
    text_norm = lambda value: re.sub(r'\s+', ' ', normal(value)).strip()
    nodes = [n for n in (lists[0].get('children') or []) if n.get('role') == 'list-item'
             and not re.fullmatch(r'\d{1,2}:\d{2}', text_norm(n.get('name')))]
    if not nodes or not raw:
        return None, area, None
    db_text = [text_norm(m.get('content')) for m in raw]
    ui_text = [text_norm(n.get('name')) for n in nodes]
    aligned = {}
    for block in SequenceMatcher(None, db_text, ui_text, autojunk=False).get_matching_blocks():
        anchors = db_text[block.a:block.a + block.size]
        if block.size >= 2 and any(len(a) > 1 and db_text.count(a) == 1 for a in anchors):
            for offset in range(block.size):
                aligned[block.a + offset] = nodes[block.b + offset]
    for idx in range(len(raw) - 1, -1, -1):
        m = raw[idx]
        if m.get('isSelf') is not False or m.get('sender') != pat_target:
            continue
        if m.get('type') not in (1, 3, 47, 49):
            continue
        chosen = aligned.get(idx)
        if chosen is None and m.get('type') == 1:
            t = text_norm(m.get('content'))
            if t and {raw[i].get('sender') for i in range(len(raw)) if db_text[i] == t} == {pat_target}:
                matches = [n for n in nodes if text_norm(n.get('name')) == t]
                if len(matches) == 1 or (matches and db_text.count(t) == 1):
                    chosen = matches[-1]
        if chosen is None:
            continue
        b = chosen.get('bounds') or {}
        if not all(k in b for k in ('x', 'y', 'width', 'height')) or not all(k in area for k in ('x', 'y', 'width', 'height')):
            continue
        if b['y'] < area['y'] - 2:
            continue
        cap_x = max(round(area['x']), round(b['x']))
        cap_y = max(round(area['y']), round(b['y']))
        cap_right = min(round(area['x'] + area['width']), round(b['x'] + b['width']), cap_x + 96)
        cap_bottom = min(round(area['y'] + area['height']), round(b['y'] + b['height']), cap_y + 84)
        cap_w, cap_h = cap_right - cap_x, cap_bottom - cap_y
        if cap_w >= 64 and cap_h >= 28:
            return chosen, area, (cap_x, cap_y, cap_w, cap_h)
    return None, area, None


def _capture_pat_avatar(capture_rect, path):
    """Capture the candidate row left gutter with scrot and locate the avatar center."""
    import struct
    from moments_layout import png_rows

    cap_x, cap_y, cap_w, cap_h = capture_rect
    capture_path = path.with_name(f"{path.stem}-pat-{os.getpid()}-{time.monotonic_ns()}.png")
    try:
        run_process(['scrot', '-a', f'{cap_x},{cap_y},{cap_w},{cap_h}', str(capture_path)],
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        header = capture_path.read_bytes()[:24]
        if header[:8] != b'\x89PNG\r\n\x1a\n' or struct.unpack('>II', header[16:24]) != (cap_w, cap_h):
            return None
        width, height, rows = png_rows(capture_path)
    finally:
        try:
            if capture_path.exists():
                capture_path.unlink()
        except OSError:
            pass
    detected = _detect_left_avatar(width, height, rows)
    if not detected:
        return None
    rel_x, rel_y, box = detected
    return cap_x + rel_x, cap_y + rel_y, box


def send_native_pat(job, path, root, name, is_group, target_id):
    """Double-click target member's avatar in the visible chat viewport to pat them."""
    global SKIP_MESSAGES
    pat_target = str(job.get('target_id') or '').strip()
    if not pat_target:
        job.update(state='done', pat_skipped=True, reason='缺少拍一拍目标用户ID')
        return root
    prev_skip = SKIP_MESSAGES
    try:
        has_msg_list = any(n.get('role') == 'list' and n.get('name') == 'Messages' and n.get('children')
                           for n in walk(root)) if root else False
        if SKIP_MESSAGES or not has_msg_list:
            SKIP_MESSAGES = False
            root = tree()
        raw = sorted(native_read('/messages', chat=target_id, limit=100)['messages'],
                     key=lambda r: (r['timestamp'], r['localId']))
        before_pat_id = max((int(r.get('localId') or 0) for r in raw), default=0)
        chosen, area, capture_rect = _locate_pat_row(raw, root, pat_target)
        if chosen is None and area and area.get('width', 0) > 0 and area.get('height', 0) > 0:
            run_process(['xdotool', 'mousemove', str(round(area['x'] + area['width'] / 2)),
                         str(round(area['y'] + area['height'] / 2)),
                         'click', '--repeat', '40', '--delay', '5', '5'], check=True, timeout=3)
            time.sleep(.12)
            root = tree()
            chosen, area, capture_rect = _locate_pat_row(raw, root, pat_target)
        if chosen is None or not capture_rect:
            job.update(state='done', pat_skipped=True, reason='未找到对方可见气泡，已自动跳过拍一拍')
            return root
        avatar = _capture_pat_avatar(capture_rect, path)
        if avatar is None:
            job.update(state='done', pat_skipped=True, reason='目标气泡左侧头像校验未命中，已自动跳过拍一拍')
            return root
        abs_x, abs_y, _ = avatar
        b = chosen['bounds']
        if not (area['x'] + 4 <= abs_x <= area['x'] + area['width'] - 4 and
                area['y'] + 4 <= abs_y <= area['y'] + area['height'] - 4 and
                b['x'] <= abs_x <= b['x'] + b['width'] and b['y'] <= abs_y <= b['y'] + b['height']):
            job.update(state='done', pat_skipped=True, reason='头像点击坐标越界，已自动跳过拍一拍')
            return root
        SKIP_MESSAGES = True
        checked = tree()
        if not selected(checked, name, is_group):
            raise ValueError('Destination changed before pat')
        if job.get('fast_session_nonce'):
            from native_fast_turn_v1 import check_guard
            check_guard(job, path)
        job['state'] = 'sending'
        path.write_text(json.dumps(job, ensure_ascii=False))
        run_process(['xdotool', 'mousemove', str(abs_x), str(abs_y), 'click', '--repeat', '2', '--delay', '90', '1'],
                    check=True, timeout=3)
        job['state'] = 'submitted'
        deadline = time.monotonic() + .9
        while time.monotonic() < deadline:
            rows = native_read('/messages', chat=target_id, limit=15)['messages']
            confirmed = [
                r for r in rows
                if int(r.get('localId') or 0) > before_pat_id
                and r.get('event') == 'pat' and r.get('isSelf') is True
                and (r.get('pat') or {}).get('target') == pat_target
            ]
            if len(confirmed) == 1:
                job['pat_confirmed'] = True
                job['pat_local_id'] = int(confirmed[0]['localId'])
                break
            time.sleep(.15)
        return None if prev_skip else checked
    finally:
        SKIP_MESSAGES = prev_skip


def send_native_text(job,path,root,name,is_group,target_id):
    text=job.get('text')
    if not isinstance(text,str) or not text.strip() or len(text)>2000:raise ValueError('Invalid text input')
    if SKIP_MESSAGES and job.get('fast_session_nonce'):
        root=tree(chat_root=root,chat_name=name,is_group=is_group)
        if not selected(root,name,is_group):raise ValueError('Destination changed before typing')
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
    checked=(tree(chat_root=root,chat_name=name,is_group=is_group,require_focus=True)
             if SKIP_MESSAGES and job.get('fast_session_nonce') else tree())
    if not selected(checked,name,is_group):raise ValueError('Destination changed before send')
    if job.get('fast_session_nonce'):
        from native_fast_turn_v1 import check_guard
        check_guard(job,path)
    # Claim and persist the non-idempotent boundary before pressing Return once.
    job['state']='sending';path.write_text(json.dumps(job,ensure_ascii=False))
    run_process(['xdotool','key','--clearmodifiers','Return'],check=True,timeout=3)
    job['submitted_at']=time.time()
    job['state']='submitted'
    return checked


def run_job(path, target_id, name, is_group, root=None):
    job=json.loads(path.read_text())
    if job.get('chat')!=target_id or not 0<=time.time()-job.get('created',0)<30:
        raise ValueError('UI job destination or age is invalid')
    if job.get('operation') not in ['send_session','send_text','send_image','send_sticker','send_pat','collect_sticker','copy_image','read_image','open_chat','inspect_quote_menu']:
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
    elif job['operation']=='send_session':
        from native_fast_turn_v1 import run_session
        run_session(job,path,root,target_id,name,is_group,tree,selected,send_native_text,send_native_sticker,PROFILE,window_signature,pat_sender=send_native_pat)
    elif job['operation']=='send_pat':
        send_native_pat(job,path,root,name,is_group,target_id)
    elif job['operation']=='send_sticker':
        send_native_sticker(job,path,root,name,is_group)
    elif job['operation']=='send_image':
        from native_image_send import send_image
        send_image(job,path,root,name,is_group)
    elif job['operation']=='send_text':
        send_native_text(job,path,root,name,is_group,target_id)
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
               and re.match(re.escape(name) + r'(?=\s|$)', normal(n.get('name'))) is not None]
    if len(matches) > 1:
        raise ValueError('Target chat is not uniquely visible in the native chat list')
    if not matches:
        return lists[0], None
    # Qt exposes partially recycled rows outside the viewport too. Never click
    # their full geometric center: it can be in the search box or another pane.
    viewport=lists[0].get('bounds') or {}
    row=matches[0];bounds=row.get('bounds') or {}
    left=max(bounds.get('x',0),viewport.get('x',0));top=max(bounds.get('y',0),viewport.get('y',0))
    right=min(bounds.get('x',0)+bounds.get('width',0),viewport.get('x',0)+viewport.get('width',0))
    bottom=min(bounds.get('y',0)+bounds.get('height',0),viewport.get('y',0)+viewport.get('height',0))
    if right-left<12 or bottom-top<12:
        return lists[0],None
    return lists[0],{**row,'bounds':{'x':left,'y':top,'width':right-left,'height':bottom-top}}


def locate_chat(root, name):
    """Only scroll the chat list, keeping the normal visible-target path immediate."""
    listing, candidate = visible_chat(root, name)
    if candidate:
        return candidate
    bounds = listing.get('bounds') or {}
    if not bounds.get('width') or not bounds.get('height'):
        raise ValueError('Native chat list has no usable bounds')
    x, y = round(bounds['x'] + bounds['width'] / 2), round(bounds['y'] + bounds['height'] / 2)
    def signature(value):
        return tuple((normal(n.get('name')),(n.get('bounds') or {}).get('y'),
                      (n.get('bounds') or {}).get('height'))
                     for n in value.get('children') or [] if n.get('role')=='list-item')
    started=time.monotonic()
    # A burst of 80 wheel ticks at 1ms was coalesced by the pinned Qt client.
    # It often stopped halfway up, then the old search only visited older rows.
    # Observe paced progress in both directions; one unchanged snapshot can be
    # an animation delay, and does not establish a boundary.
    for direction,rounds,steps in (('4',10,'10'),('5',20,'4')):
        previous=signature(listing);stable=0
        for _ in range(rounds):
            if time.monotonic()-started>12:
                raise ValueError('微信会话定位超时，尚未发送')
            run_process(['xdotool','mousemove',str(x),str(y),'click','--repeat',steps,'--delay','25',direction],
                        check=True,timeout=3)
            PROFILE['chat_scrolls']=PROFILE.get('chat_scrolls',0)+1
            time.sleep(.12)
            listing,candidate=visible_chat(tree(),name)
            if candidate:
                PROFILE['chat_search_seconds']=time.monotonic()-started
                return candidate
            current=signature(listing)
            stable=stable+1 if current==previous else 0
            if stable>=2:break
            previous=current
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
        if job_spec.get('operation') == 'reset_ui':
            from native_recovery import run
            run(job_path, target_id)
            return {'ok':True, 'username':target_id, 'skipped':True}
        SKIP_MESSAGES = (str(job_spec.get('operation','')).startswith('moments_') or job_spec.get('operation') in ('send_session','send_sticker','send_image','open_chat') or
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
    version_file = Path('/data/runtime-version.json')
    modern = version_file.is_file() and json.loads(version_file.read_text()).get('wechat_version') == '4.1.13.26'
    if modern:
        from native_voice_compat import restore_main
        restore_main(sys.modules[__name__])
    root = tree()
    moments_frames=[n for n in walk(root) if n.get('role')=='frame' and n.get('name')=='Moments']
    if moments_frames:
        from moments_ui import editors, button
        if len(moments_frames)!=1:
            raise ValueError('Cannot uniquely locate Moments window; draft preserved')
        if editors(moments_frames[0]):
            windows=run_process(['xdotool','search','--onlyvisible','--name','^Moments$'],
                                capture_output=True,text=True,timeout=3)
            ids=[value for value in windows.stdout.split() if value.isdigit()]
            if windows.returncode not in (0,1) or len(ids)!=1:
                raise ValueError('Cannot uniquely locate visible Moments editor; draft preserved')
            run_process(['xdotool','windowminimize',ids[0]],check=True,timeout=3)
            time.sleep(.15)
            root=tree()
            remaining=[n for n in walk(root) if n.get('role')=='frame' and n.get('name')=='Moments']
            if any(editors(n) for n in remaining):
                raise ValueError('Moments editor did not minimize; draft preserved')
        else:
            click_node(button(moments_frames[0],'Close'))
        time.sleep(.12)
        root=tree()
    if selected(root, name, is_group):
        return finish(True)
    try:
        b = locate_chat(root, name).get("bounds") or {}
    except ValueError:
        if not modern or not is_group:
            raise
        from native_voice_compat import open_group_by_search
        open_group_by_search(sys.modules[__name__], name)
        if selected(tree(), name, is_group):
            return finish(False)
        raise ValueError('群聊搜索后未确认目标会话，未发送')
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
