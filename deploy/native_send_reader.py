"""Fresh chat controls over the existing locked AT-SPI connection protocol.

Only object references are reused. Parent membership, selection, names, bounds
and composer focus are read again before a text submission.
"""
import importlib
from pathlib import Path
import re
import sys
import time
import unicodedata

NATIVE_DIRECTORY = Path(__file__).resolve().parent
if str(NATIVE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(NATIVE_DIRECTORY))
STATE_DIRECTORY = Path('/data/native-tools/a11y-state')
_BUS = None


def _discard_bus():
    global _BUS
    bus, _BUS = _BUS, None
    if bus is not None:
        try:
            bus.connection.close_sync(None)
        except Exception:
            pass


def _normal(value):
    return unicodedata.normalize('NFKC', str(value or '')).strip()


def _controls(bus, safe, root, name, is_group, require_focus):
    paths = []
    def walk(node, path=()):
        paths.append((node, path + (node,)))
        for child in node.get('children') or []:
            walk(child, path + (node,))
    walk(root)
    lists = [n for n, _ in paths if n.get('role') == 'list' and n.get('name') == 'Chats']
    if len(lists) != 1:
        raise ValueError('Native chat list changed')
    chat_list = lists[0]
    list_path = next(path for node, path in paths if node is chat_list)
    frame = next((n for n in reversed(list_path) if n.get('role') == 'frame'), None)
    def in_frame(path):
        return frame is None or any(n is frame for n in path)
    chosen = [n for n in chat_list.get('children', ()) if 'SELECTED' in n.get('states', ())]
    right = (chat_list.get('bounds') or {}).get('x', 0) + (chat_list.get('bounds') or {}).get('width', 0)
    def header(n):
        title = _normal(n.get('name'))
        matches = title == _normal(name) or is_group and re.fullmatch(re.escape(_normal(name)) + r'\s*[（(]\s*\d+\s*[)）]', title)
        return (n.get('role') == 'label' and matches and (n.get('bounds') or {}).get('x', -1) > right
                and (n.get('bounds') or {}).get('y', 1000) < (chat_list.get('bounds') or {}).get('y', 100))
    headers = [n for n, path in paths if in_frame(path) and header(n)]
    editors = [n for n, path in paths if in_frame(path) and n.get('role') == 'text' and 'EDITABLE' in n.get('states', ())
               and (n.get('bounds') or {}).get('height', 0) > 35 and (n.get('bounds') or {}).get('y', 0) > 200]
    if len(chosen) != 1 or not 1 <= len(headers) <= 4 or len(editors) != 1:
        raise ValueError(f'Native chat controls are not unique (rows={len(chosen)}, headers={len(headers)}, editors={len(editors)})')
    wanted = [chat_list, chosen[0], *headers, editors[0]]
    identities = {id(n) for n in wanted}
    edges = set()
    for node, path in paths:
        if id(node) not in identities:
            continue
        refs = [safe.parse_sticker_panel_ref(n.get('native_ref')) for n in path]
        edges.update(zip(refs, refs[1:]))
    children = {}
    for parent, child in edges:
        if parent not in children:
            children[parent] = set(bus.children(parent))
        if child not in children[parent]:
            raise ValueError('Native chat control parent changed')
    fresh = []
    for old in wanted:
        ref = safe.parse_sticker_panel_ref(old.get('native_ref'))
        role = bus.role(ref)
        bounds = bus.bounds(ref)
        if role != old['role'] or not bounds:
            raise ValueError('Native chat control changed')
        fresh.append({'role': role, 'name': bus.properties(ref).get('Name', '') or '',
                      'bounds': bounds, 'states': bus.states(ref, role), 'native_ref': old['native_ref']})
    live_list, live_row, live_editor = fresh[0], fresh[1], fresh[-1]
    if 'SELECTED' not in live_row['states'] or 'EDITABLE' not in live_editor['states']:
        raise ValueError('Native chat selection or composer changed')
    chat_list = live_list
    right = live_list['bounds']['x'] + live_list['bounds']['width']
    if (not re.match(re.escape(_normal(name)) + r'(?=\s|$)', _normal(live_row['name']))
            or not any(header(n) for n in fresh[2:-1])):
        raise ValueError('Native chat destination changed')
    if require_focus and 'FOCUSED' not in live_editor['states']:
        raise ValueError('Native composer focus changed')
    live_list['children'] = [live_row]
    replacements = {id(old): new for old, new in zip(wanted, fresh)}
    keep = set()
    for node, path in paths:
        if id(node) in identities:
            keep.update(id(parent) for parent in path)
    def prune(node):
        if id(node) not in keep:
            return None
        result = dict(replacements.get(id(node), node))
        nested = [child for child in (prune(n) for n in node.get('children', ())) if child]
        result.pop('children', None)
        if nested:
            result['children'] = nested
        return result
    result = prune(root)
    result['send_controls_only'] = True
    return result, {
        'nodes': len(fresh), 'bulk_requests': len(children), 'child_index_requests': 0,
        'stale_nodes_skipped': 0, 'send_controls_read': True}


def read(root=None, *, name='', is_group=False, require_focus=False):
    global _BUS
    safe = importlib.import_module('a11y_safe')
    started = time.monotonic()
    acquired = False
    with (STATE_DIRECTORY/'read.lock').open('r+') as lock:
        try:
            safe.acquire_read_lock(lock)
            acquired = True
            locked = time.monotonic()
            deadline = started + 6.5
            if _BUS is None:
                _BUS = safe.A11yBus(deadline=deadline)
            _BUS.deadline = deadline
            if root is None:
                value, stats = safe.dump_tree(_BUS, skip_messages=True, deadline=deadline)
            else:
                value, stats = _controls(_BUS, safe, root, name, is_group, require_focus)
            if not value:
                raise ValueError('Native chat snapshot is unavailable')
            stats.update(at=time.time(), ok=True, send_read=True,
                         elapsed_seconds=round(time.monotonic()-started, 4),
                         lock_wait_seconds=round(locked-started, 4))
            safe.publish_stats(STATE_DIRECTORY, stats)
            return value
        except BaseException:
            _discard_bus()
            raise
        finally:
            if acquired:
                safe.fcntl.flock(lock, safe.fcntl.LOCK_UN)
