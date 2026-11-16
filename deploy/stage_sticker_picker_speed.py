"""Create a narrow, checksum-guarded sticker-picker release payload."""
import base64
import argparse
import ast
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import tarfile

from deploy_sticker_picker_speed import release_snapshot
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/sticker-picker-speed-20261107-06'
REMOTE = 'root@120.46.186.50'
REMOTE_DIR = '/opt/whalegirl-linux-assets/sticker-picker-speed-20261107-06'
FILES = [
    'whalegirl_linux/delivery.py',
    'deploy/chat_select_linux.py',
    'tests/test_native_favorite_mapping.py',
    'tests/test_sticker_grid_safety.py',
    'tests/test_sticker_misdelivery.py',
]
EXPECTED = {
    'whalegirl_linux/delivery.py': '7ee10f22590deee333661320fb464896a1c9206c8ae5de27a788ee0ba07e5e62',
    'deploy/chat_select_linux.py': '6eb1f37a223b6198bb4de2074ea86efea62375f8ec44f3aa5ad7b3658c8760e4',
    'tests/test_native_favorite_mapping.py': '13d0bbb498a4574a643f4200b20c7bd2dfa7f2c6bb9a9ec37d4883cffba07849',
    'tests/test_sticker_grid_safety.py': '946cd116476a08f1a00d976a98d2011cd31ad55e92224e2acfdf27b81d4b9190',
    'tests/test_sticker_misdelivery.py': 'eb213901397cfa78adcf1e7a3b92f2de5a4b351c914b9bc7130a564d1b1256d6',
}


def remote(code):
    return subprocess.run(
        ['ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
         'root@120.46.186.50', 'python3', '-'],
        input=code, capture_output=True, text=True, encoding='utf-8',
        check=True, timeout=60,
    ).stdout


def live_files():
    code = (
        'import base64,json,pathlib,os,hashlib\nfrom pathlib import Path\n'
        + inspect.getsource(release_snapshot) + '\n'
        "root=pathlib.Path('/opt/whalegirl-linux/current').resolve()\n"
        f'names={FILES!r}\n'
        "print(json.dumps({'root':str(root),'source_snapshot':release_snapshot(root),"
        "'files':{n:base64.b64encode((root/n).read_bytes()).decode() for n in names}}))"
    )
    return json.loads(remote(code))


def replace_once(text, old, new, label):
    if new and new in text:
        return text
    if text.count(old) != 1:
        raise RuntimeError(f'Live anchor is not unique: {label}')
    return text.replace(old, new, 1)


def transform(name, source):
    value = source
    if name == 'whalegirl_linux/delivery.py':
        old = 'import time\n\nimport httpx\n'
        new = 'import time\nfrom datetime import datetime\n\nimport httpx\n'
        value = replace_once(value, old, new, 'timestamp parser import')
        old = '''def misdelivered_stickers(details, rows):
    """Own sticker rows from this submission that carry a different favorite."""
    from .gateway import outbound_identity

    expected = str(details.get('md5') or '').lower()
    return [m for m in rows if m.get('isSelf') is True and m.get('type') == 47
            and outbound_identity(m) not in details.get('before_ids', ())
            and str((m.get('sticker') or {}).get('md5') or '').lower() not in ('', expected)]
'''
        new = '''def misdelivered_stickers(details, rows):
    """Own, acknowledged sticker rows from this submission with another favorite."""
    from .gateway import outbound_identity, server_acknowledged

    expected = str(details.get('md5') or '').lower()
    submitted_at = float(details.get('submitted_at') or details.get('started_at') or 0)
    before_local_id = int(details.get('before_local_id') or 0)
    return [m for m in rows if m.get('isSelf') is True and m.get('type') == 47
            and outbound_identity(m) not in details.get('before_ids', ())
            and int(m.get('localId') or 0) > before_local_id
            and server_acknowledged(m)
            and (not sticker_message_timestamp(m) or
                 submitted_at - 2 <= sticker_message_timestamp(m) <= time.time() + 1)
            and str((m.get('sticker') or {}).get('md5') or '').lower() not in ('', expected)]


def sticker_message_timestamp(message):
    value = message.get('timestamp')
    if isinstance(value, (float, int)):
        return value / 1000 if value > 10**12 else float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except ValueError:
            return 0
    return 0
'''
        value = replace_once(value, old, new, 'strictly attribute a misdelivered sticker')
        old = '''def invalidate_sticker_anchors(gateway):
    """A wrong tile proves the saved object-to-index anchors are stale."""
    directory = getattr(gateway, 'ui_jobs_directory', None)
    if not directory:
        return False
    try:
        (directory / STICKER_ANCHOR_CACHE).unlink()
        return True
    except OSError:
        return False
'''
        new = '''def invalidate_sticker_anchors(gateway):
    """Mark stale anchors unusable while retaining the cache file and evidence."""
    directory = getattr(gateway, 'ui_jobs_directory', None)
    if not directory:
        return False
    path = directory / STICKER_ANCHOR_CACHE
    try:
        cached = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(cached, dict):
            cached = {'previous_cache': cached}
        cached['invalidated'] = True
        cached['invalidated_at'] = time.time()
        path.write_text(json.dumps(cached, ensure_ascii=False), encoding='utf-8')
        return True
    except (OSError, ValueError, TypeError):
        return False
'''
        value = replace_once(value, old, new, 'preserve and mark stale anchor cache')
        old = '''    except (OSError, ValueError, TypeError):
        return False


def matches_message(kind, details, row):
'''
        new = '''    except (OSError, ValueError, TypeError):
        return False


def misdelivered_sticker_receipt(gateway, details, row, attempt):
    from .gateway import Receipt

    wrong_md5 = str((row.get('sticker') or {}).get('md5') or '')
    details.update(wrong_sticker_md5=wrong_md5,
                   local_id=int(row.get('localId') or 0),
                   anchors_invalidated=invalidate_sticker_anchors(gateway))
    return Receipt(False, error='发错表情：微信实际发出了另一张收藏表情，已标记定位缓存失效，不重发',
                   definite_failure=True, retryable=False, attempts=attempt, details=details)


def matches_message(kind, details, row):
'''
        value = replace_once(value, old, new, 'acknowledged wrong-sticker receipt')
        old = '''                    if kind == 'sticker' and rows:
                        misdelivered = misdelivered_stickers(details, rows)
'''
        new = '''                    if kind == 'sticker' and rows and not fresh:
                        misdelivered = misdelivered_stickers(details, rows)
                        if len(misdelivered) == 1:
                            return misdelivered_sticker_receipt(gateway, details, misdelivered[0], attempt)
'''
        value = replace_once(value, old, new, 'confirm wrong sticker promptly')
        old = '''                if kind == 'sticker' and len(misdelivered) == 1:
                    # WeChat accepted a click on a neighbouring favorite. It is
                    # already visible, so record it truthfully, never resend, and
                    # drop the anchors that led the picker to the wrong tile.
                    wrong = misdelivered[0]
                    details.update(wrong_sticker_md5=str((wrong.get('sticker') or {}).get('md5') or ''),
                                   local_id=int(wrong.get('localId') or 0),
                                   anchors_invalidated=invalidate_sticker_anchors(gateway))
                    return Receipt(False, error='发错表情：微信实际发出了另一张收藏表情，已作废定位缓存，不重发',
                                   definite_failure=True, retryable=False, attempts=attempt, details=details)
'''
        value = replace_once(value, old, '', 'remove delayed misdelivery report')
    elif name == 'deploy/chat_select_linux.py':
        old = '''    if len(tiles)==total:
        known.update({n['native_ref']:i for i,n in enumerate(tiles) if n.get('native_ref')})
        return tiles[index] if 0<=index<len(tiles) else None
    # A final full-tree refresh can briefly report conflicting offsets while
    # the same virtual tile objects are being recycled.  If the requested
    # object's stable native identity is already known, selecting that exact
    # object is safe and avoids guessing from a transient page offset.
    target_refs=[ref for ref,absolute in known.items() if absolute==index]
    if len(target_refs)==1:
        target_ref=target_refs[0]
        exact=[n for n in tiles if n.get('native_ref')==target_ref]
        if len(exact)==1:
            return exact[0]
    offsets={known[n['native_ref']]-i for i,n in enumerate(tiles) if n.get('native_ref') in known}
'''
        new = '''    if len(tiles)==total:
        known.update({n['native_ref']:i for i,n in enumerate(tiles) if n.get('native_ref')})
        return tiles[index] if 0<=index<len(tiles) else None
    offsets={known[n['native_ref']]-i for i,n in enumerate(tiles) if n.get('native_ref') in known}
'''
        value = replace_once(value, old, new, 'reject transient exact-ref shortcut')
        old = '''        if (panel_ref and cached.get('panel_ref')==panel_ref and cached.get('order')==order
                and 0<=time.time()-cached.get('updated',0)<1800 and cached.get('grid')):
'''
        new = '''        if (panel_ref and not cached.get('invalidated') and cached.get('panel_ref')==panel_ref
                and cached.get('order')==order
                and 0<=time.time()-cached.get('updated',0)<1800 and cached.get('grid')):
'''
        value = replace_once(value, old, new, 'honor invalidated anchor cache')
        old = '''        wheel(area,direction,3,5,'boundary_check');time.sleep(.08)
        again=refresh_panel();again_tiles,again_area=panel(again)
        if favorite_page_signature(again_tiles)!=favorite_page_signature(tiles):
            raise ValueError('Native favorite boundary did not settle')
'''
        new = '''        for edge_attempt in range(2):
            wheel(area,direction,3,5,'boundary_check');time.sleep(.08)
            again=refresh_panel();again_tiles,again_area=panel(again)
            if favorite_page_signature(again_tiles)==favorite_page_signature(tiles):
                root,tiles,area=again,again_tiles,again_area
                break
            # The first long scroll may stop one row short while Qt is finishing
            # its animation. Re-anchor and prove the edge again before selection.
            root,tiles,area=again,again_tiles,again_area
            if edge_attempt==1:
                raise ValueError('Native favorite boundary did not settle')
            wheel(area,direction,40,5,'boundary_scroll');time.sleep(.15)
            settled=False
            for _ in range(5):
                settle_root=refresh_panel();settle_tiles,settle_area=panel(settle_root)
                if favorite_page_signature(settle_tiles)==favorite_page_signature(tiles):
                    root,tiles,area=settle_root,settle_tiles,settle_area
                    settled=True
                    break
                root,tiles,area=settle_root,settle_tiles,settle_area
                time.sleep(.06)
            if not settled:
                raise ValueError('Native favorite boundary did not settle')
'''
        value = replace_once(value, old, new, 'retry and verify transient boundary movement')
        old = '''                    far=relative< -3*len(grid['xs'])
                    wheel(area,'4',2 if far else 1,20,'alignment_scroll')
'''
        new = '''                    far=relative< -2*len(grid['xs'])
                    visible_rows=max(1,(len(tiles)+len(grid['xs'])-1)//len(grid['xs']))
                    repeat=min(3,max(1,visible_rows-1)) if far else 1
                    wheel(area,'4',repeat,20,'alignment_scroll')
'''
        value = replace_once(value, old, new, 'use bounded overlapping scroll batches')
        old = "                    if stagnant>=3 or aligned and first_index==before_index and relative>=len(tiles):break\n"
        new = "                    if stagnant>=3:break\n"
        value = replace_once(value, old, new, 'allow transient no-movement retry')
    elif name == 'tests/test_sticker_grid_safety.py':
        old = "        self.assertEqual(firsts, sorted(firsts, reverse=True))\n"
        new = old + "        self.assertLessEqual(len(job['favorite_scroll']), 4, 'far target should use overlapping multi-notch scrolls')\n"
        value = replace_once(value, old, new, 'assert reduced alignment snapshots')
        old = '''    def test_boundary_that_stops_short_is_not_trusted(self):
        for target in (2, 19, 70, 89):
            grid = FakeGrid(short_boundary=True)
            job, clicks, error = send(grid, target, workspace())
            self.assert_exact_or_refused(job, clicks, error, target)
'''
        new = '''    def test_boundary_that_stops_short_is_retried_then_verified(self):
        for target in (2, 19, 70, 89):
            grid = FakeGrid(short_boundary=True)
            job, clicks, error = send(grid, target, workspace())
            self.assertIsNone(error, (target, error))
            self.assertEqual(job['state'], 'submitted')
            self.assertEqual(clicks, [target])
'''
        value = replace_once(value, old, new, 'verify safe boundary retry')
    elif name == 'tests/test_native_favorite_mapping.py':
        old = '''    def test_exact_known_target_survives_transient_offset_conflict(self):
        tiles=[{'native_ref':'a'},{'native_ref':'target'},{'native_ref':'c'}]
        self.assertIs(favorite_tile(tiles,5,20,{'target':5,'a':1,'c':7}),tiles[1])
'''
        new = '''    def test_conflicting_offsets_refuse_even_if_target_anchor_exists(self):
        tiles=[{'native_ref':'a'},{'native_ref':'target'},{'native_ref':'c'}]
        self.assertIsNone(favorite_tile(tiles,5,20,{'target':5,'a':1,'c':7}))
'''
        value = replace_once(value, old, new, 'reject ambiguous target anchor')
    elif name == 'tests/test_sticker_misdelivery.py':
        value = replace_once(value, 'import asyncio\n', 'import asyncio\nimport json\n', 'cache test json import')
        value = replace_once(
            value,
            "        (self.ui_jobs_directory / STICKER_ANCHOR_CACHE).write_text('{}')\n",
            "        (self.ui_jobs_directory / STICKER_ANCHOR_CACHE).write_text(json.dumps({\n"
            "            'updated': 1, 'anchors': {'tile:old': 3}, 'grid': {'xs': [0], 'shift': 0}}))\n",
            'preserve original anchor cache in test',
        )
        value = replace_once(
            value,
            '    async def test_wrong_favorite_is_a_final_truthful_failure_and_drops_anchors(self):\n',
            '    async def test_wrong_favorite_is_reported_and_marks_anchors_stale(self):\n',
            'rename stale cache behavior test',
        )
        value = replace_once(
            value,
            "        self.assertFalse((gateway.ui_jobs_directory / STICKER_ANCHOR_CACHE).exists())\n",
            "        cache = json.loads((gateway.ui_jobs_directory / STICKER_ANCHOR_CACHE).read_text())\n"
            "        self.assertTrue(cache['invalidated'])\n"
            "        self.assertIn('invalidated_at', cache)\n",
            'assert cache retained but invalidated',
        )
        old = "    return {'isSelf': True, 'type': 47, 'localId': 101, 'serverId': server_id, 'sticker': {'md5': md5}}\n"
        new = "    return {'isSelf': True, 'type': 47, 'localId': 101, 'serverId': server_id,\n            'timestamp': __import__('time').time(), 'sticker': {'md5': md5}}\n"
        value = replace_once(value, old, new, 'timestamp misdelivery test row')
        value = replace_once(value, 'import tempfile\nimport unittest\n',
                             'import tempfile\nimport time\nimport unittest\n', 'test timestamp import')
        value = replace_once(value, 'from whalegirl_linux.delivery import STICKER_ANCHOR_CACHE, send_native\n',
                             'from whalegirl_linux.delivery import STICKER_ANCHOR_CACHE, misdelivered_stickers, send_native\n',
                             'test attribution helper import')
    additions = {
        'tests/test_sticker_grid_safety.py': (
            ('test_explicitly_invalidated_cache_is_ignored_even_with_a_grid',),
            '    def test_poisoned_cache_with_grid_never_clicks_a_neighbour(self):'),
        'tests/test_sticker_misdelivery.py': (
            ('test_correct_receipt_wins_over_another_new_own_sticker',
             'test_old_unacknowledged_and_baseline_rows_are_not_misdelivery'),
            '    async def test_missing_row_stays_uncertain_not_misdelivered(self):'),
    }
    if name in additions:
        methods, marker = additions[name]
        local = (ROOT / name).read_text(encoding='utf-8')
        nodes = {node.name:node for node in ast.walk(ast.parse(local))
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        lines = local.splitlines()
        extra = ''.join('\n'.join(lines[nodes[method].lineno-1:nodes[method].end_lineno]) + '\n\n'
                        for method in methods)
        value = replace_once(value, marker, extra + marker, 'targeted added regression tests')
    compile(value, name, 'exec')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deploy', action='store_true', help='Upload, test and deploy after staging')
    args = parser.parse_args()
    if OUT.exists():
        raise RuntimeError(f'Refusing to overwrite existing staging artifacts: {OUT}')
    live = live_files()
    before, after = {}, {}
    (OUT / 'payload').mkdir(parents=True, exist_ok=True)
    (OUT / 'server-before').mkdir(parents=True, exist_ok=True)
    for name in FILES:
        raw = base64.b64decode(live['files'][name])
        before[name] = hashlib.sha256(raw).hexdigest()
        if before[name] != EXPECTED[name]:
            raise RuntimeError(f'Unexpected live baseline for {name}; review before staging')
        (OUT / 'server-before' / name).parent.mkdir(parents=True, exist_ok=True)
        (OUT / 'server-before' / name).write_bytes(raw)
        transformed = transform(name, raw.decode('utf-8'))
        target = OUT / 'payload' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(transformed, encoding='utf-8', newline='\n')
        after[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = {'root': live['root'], 'before': before, 'after': after,
                'source_snapshot': live['source_snapshot'],
                'files': FILES, 'strategy': 'isolated release copy; code and database rollback snapshot'}
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    with tarfile.open(OUT / 'release.tar', 'w') as archive:
        for name in FILES:
            archive.add(OUT / 'payload' / name, arcname=name)
    for name in FILES:
        local = (ROOT / name).read_bytes()
        staged = (OUT / 'payload' / name).read_bytes()
        if local != staged:
            raise RuntimeError(f'Staged output differs from reviewed local source: {name}')
    if not args.deploy:
        print(json.dumps({'root':live['root'], 'staged_only':True,
                          'source_files':len(live['source_snapshot']), 'files':FILES,
                          'manifest':str(OUT / 'manifest.json')}, ensure_ascii=False))
        return
    subprocess.run(['ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', REMOTE,
                    'mkdir', '-m', '700', REMOTE_DIR], check=True, timeout=30)
    subprocess.run(['scp.exe', '-q', str(OUT / 'release.tar'), str(OUT / 'manifest.json'),
                    str(ROOT / 'deploy/deploy_sticker_picker_speed.py'), REMOTE + ':' + REMOTE_DIR + '/'],
                   check=True, timeout=120)
    result = subprocess.run(
        ['ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', REMOTE,
         'python3', REMOTE_DIR + '/deploy_sticker_picker_speed.py',
         REMOTE_DIR + '/release.tar', REMOTE_DIR + '/manifest.json'],
        capture_output=True, text=True, encoding='utf-8', timeout=900,
    )
    (OUT / 'deployment.stdout.log').write_text(result.stdout, encoding='utf-8')
    (OUT / 'deployment.stderr.log').write_text(result.stderr, encoding='utf-8')
    if result.returncode:
        raise RuntimeError('Candidate deployment failed: ' + (result.stdout + result.stderr)[-6000:])
    print(json.dumps({'root': live['root'], 'files': FILES,
                      'manifest': str(OUT / 'manifest.json'), 'archive_bytes': (OUT / 'release.tar').stat().st_size,
                      'deployment': json.loads(result.stdout)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
