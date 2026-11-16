"""A favorite sticker is either sent exactly or not sent at all.

Simulates WeChat's virtualized Qt favorite grid (row-major, partially exposed,
optional leading cells) and the accessibility quirks seen in production:
recycled tile objects, reads taken mid-animation, a boundary scroll that stops
short, and a poisoned anchor cache. Production clicked neighbouring stickers in
each of these situations; the selector must now click the requested favorite
or raise before any click.
"""
import importlib.util
import json
import math
from pathlib import Path
import random
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
with patch.object(sys, 'path', [str(ROOT / 'deploy'), *sys.path]):
    spec = importlib.util.spec_from_file_location('grid_safety_selector', ROOT / 'deploy' / 'chat_select_linux.py')
    selector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selector)

PANEL = ':1.42|/org/a11y/atspi/accessible/700'


class FakeGrid:
    """Row-major favorite grid behind a 4-row viewport with one buffered row."""

    def __init__(self, total=90, cols=5, shift=0, refs='stable', lag_reads=0, short_boundary=False):
        self.total, self.cols, self.shift = total, cols, shift
        self.pitch, self.size, self.view_rows = 70, 60, 4
        self.area = {'x': 0, 'y': 100, 'width': cols * self.pitch, 'height': self.view_rows * self.pitch}
        rows = math.ceil((total + shift) / cols)
        self.max_scroll = max(0, rows - self.view_rows) * self.pitch
        self.scroll = 0
        self.refs = refs
        self.lag_reads = lag_reads
        self.pending = []
        self.short_boundary = short_boundary
        self.clicked = []
        self.reads = 0
        self.rng = random.Random(7)

    def wheel(self, direction, repeat):
        step = -self.pitch if direction == '4' else self.pitch
        if repeat >= 40:
            target = 0 if direction == '4' else self.max_scroll
            if self.short_boundary:
                # Qt stops one row short on the first burst; a later wheel finishes it.
                target = self.pitch if direction == '4' else self.max_scroll - self.pitch
                self.short_boundary = False
            target = min(max(target, 0), self.max_scroll)
        else:
            target = min(max(self.scroll + step * repeat, 0), self.max_scroll)
        if self.lag_reads:
            # The first reads after a wheel still show an intermediate position.
            middle = self.scroll + (target - self.scroll) // 2 // self.pitch * self.pitch
            self.pending = [middle] * self.lag_reads
        self.scroll = target

    def visible_scroll(self):
        return self.pending.pop(0) if self.pending else self.scroll

    def tiles(self):
        self.reads += 1
        scroll = self.visible_scroll()
        first_row = max(0, scroll // self.pitch - 1)
        last_row = (scroll + self.area['height']) // self.pitch
        out = []
        for item in range(self.total):
            cell = item + self.shift
            row, col = divmod(cell, self.cols)
            if not first_row <= row <= last_row:
                continue
            out.append({'role': 'label', 'name': 'Custom Stickers', 'item': item,
                        'bounds': {'x': col * self.pitch, 'y': self.area['y'] + row * self.pitch - scroll + 5,
                                   'width': self.size, 'height': self.size}})
        out.sort(key=lambda n: (n['bounds']['y'], n['bounds']['x']))
        for position, node in enumerate(out):
            if self.refs == 'stable':
                node['native_ref'] = 'tile:%d' % node['item']
            elif self.refs == 'slots':
                # Worst case: an object belongs to a screen slot, not a favorite.
                node['native_ref'] = 'slot:%d' % position
            elif self.refs == 'partial-recycled' and position % 10 == 0:
                # A few Qt objects are reused by screen slot, the rest retain
                # stable identities. This matches the live 25/28-anchor quorum.
                node['native_ref'] = 'recycled-slot:%d' % position
            elif self.refs == 'partial-recycled':
                node['native_ref'] = 'tile:%d' % node['item']
            elif self.refs == 'shuffled':
                node['native_ref'] = 'obj:%d' % self.rng.randrange(10 ** 6)
            node.pop('item')
        return out

    def list_node(self):
        return {'role': 'list', 'native_ref': PANEL, 'bounds': dict(self.area), 'children': self.tiles()}

    def root(self):
        return {'role': 'frame', 'children': [{'role': 'page-tab', 'name': 'Custom Stickers'}, self.list_node()]}

    def tree(self, sticker_panel_ref=None):
        return self.list_node() if sticker_panel_ref else self.root()

    def command(self, argv, **options):
        if argv[0] == 'xdotool' and 'click' in argv and '--repeat' in argv:
            self.wheel(argv[-1], int(argv[argv.index('--repeat') + 1]))
        elif argv[0] == '/opt/tools/click':
            x, y = int(argv[1]), int(argv[2])
            scroll = self.scroll
            row = (y - self.area['y'] - 5 + scroll) // self.pitch
            col = x // self.pitch
            self.clicked.append(row * self.cols + col - self.shift)


def send(grid, target, folder, cache=None, *, order=None):
    path = folder / 'job.json'
    order = order if order is not None else ['%032x' % (i + 1) for i in range(grid.total)]
    if cache is not None:
        (folder / 'sticker-panel-anchors.cache').write_text(json.dumps({**cache, 'order': order}))
    job = {'md5': order[target], 'chat': 'test', 'created': time.time()}
    before = len(grid.clicked)
    with patch.object(selector, 'PROFILE', {'snapshots': 0, 'snapshot_seconds': 0.0}), \
         patch.object(selector, 'native_read', return_value={'stickers': [{'md5': m} for m in order]}), \
         patch.object(selector, 'selected', return_value=True), \
         patch.object(selector, 'run_process', side_effect=grid.command), \
         patch.object(selector, 'tree', side_effect=grid.tree), \
         patch.object(selector.time, 'sleep'):
        try:
            selector.send_native_sticker(job, path, grid.root(), 'test', True)
            error = None
        except ValueError as exc:
            error = str(exc)
    clicks = grid.clicked[before:]
    return job, clicks, error


def workspace():
    runs = ROOT / 'test-runs'
    runs.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix='sticker-grid-', dir=runs))


class StickerGridSafetyTests(unittest.TestCase):
    def assert_exact_or_refused(self, job, clicks, error, target):
        if error is None:
            self.assertEqual(job['state'], 'submitted')
            self.assertEqual(clicks, [target], 'clicked a different favorite')
        else:
            self.assertEqual(clicks, [], 'clicked before refusing: ' + error)

    def test_every_favorite_is_reached_exactly_from_a_cold_panel(self):
        for shift in (0, 1, 2):
            for target in range(90):
                grid = FakeGrid(shift=shift)
                job, clicks, error = send(grid, target, workspace())
                self.assertIsNone(error, (shift, target, error))
                self.assertEqual(clicks, [target], (shift, target))

    def test_middle_favorites_walk_from_the_nearer_verified_boundary(self):
        grid = FakeGrid()
        job, clicks, error = send(grid, 34, workspace())
        self.assertIsNone(error)
        self.assertEqual(clicks, [34])
        self.assertTrue(job['favorite_scroll'], 'expected overlapping pages from the nearer top')
        firsts = [step[1] for step in job['favorite_scroll']]
        self.assertEqual(firsts, sorted(firsts))
        self.assertLessEqual(len(job['favorite_scroll']), 4, 'far target should use overlapping multi-notch scrolls')

    def test_warm_cache_sequence_stays_exact(self):
        grid = FakeGrid(shift=2)
        folder = workspace()
        rng = random.Random(11)
        sent = 0
        for target in [rng.randrange(90) for _ in range(60)]:
            job, clicks, error = send(grid, target, folder)
            self.assert_exact_or_refused(job, clicks, error, target)
            sent += error is None
        self.assertGreaterEqual(sent, 58)
        cache = json.loads((folder / 'sticker-panel-anchors.cache').read_text())
        self.assertEqual(cache['grid']['shift'], 2)

    def test_recycled_or_meaningless_objects_never_click_a_neighbour(self):
        for refs in ('slots', 'shuffled'):
            folder = workspace()
            grid = FakeGrid(refs=refs, shift=1)
            for target in range(0, 90, 3):
                job, clicks, error = send(grid, target, folder)
                self.assert_exact_or_refused(job, clicks, error, target)

    def test_reads_taken_mid_animation_wait_for_the_settled_boundary(self):
        for target in (3, 18, 24, 60, 88):
            grid = FakeGrid(lag_reads=1)
            job, clicks, error = send(grid, target, workspace())
            self.assert_exact_or_refused(job, clicks, error, target)

    def test_few_recycled_refs_are_overruled_only_by_grid_checked_anchor_quorum(self):
        for target in (0, 17, 32, 46, 61, 78, 89):
            grid = FakeGrid(refs='partial-recycled', shift=1)
            job, clicks, error = send(grid, target, workspace())
            self.assertIsNone(error, (target, error))
            self.assertEqual(job['state'], 'submitted')
            self.assertEqual(clicks, [target])

    def test_boundary_that_stops_short_is_retried_then_verified(self):
        for target in (2, 19, 70, 89):
            grid = FakeGrid(short_boundary=True)
            job, clicks, error = send(grid, target, workspace())
            self.assertIsNone(error, (target, error))
            self.assertEqual(job['state'], 'submitted')
            self.assertEqual(clicks, [target])

    def test_poisoned_cache_without_grid_is_ignored(self):
        grid = FakeGrid()
        wrong = {'tile:%d' % i: i + 5 for i in range(90)}
        job, clicks, error = send(grid, 40, workspace(),
                                  cache={'panel_ref': PANEL, 'updated': time.time(), 'anchors': wrong})
        self.assertIsNone(error)
        self.assertEqual(clicks, [40])

    def test_explicitly_invalidated_cache_is_ignored_even_with_a_grid(self):
        grid = FakeGrid()
        wrong = {'tile:%d' % i: i + 5 for i in range(90)}
        cache = {'panel_ref': PANEL, 'updated': time.time(), 'anchors': wrong,
                 'invalidated': True, 'grid': {'xs': [0, 70, 140, 210, 280], 'shift': 0}}
        job, clicks, error = send(grid, 40, workspace(), cache=cache)
        self.assertIsNone(error)
        self.assertEqual(clicks, [40])

    def test_poisoned_cache_with_grid_never_clicks_a_neighbour(self):
        # Production cache poisoning came from mis-aligned pages (offsets such as
        # 2, 13, 42). Any offset that is not a whole row contradicts the
        # calibrated columns and must be refused rather than clicked.
        for offset in (1, 2, 3, 13, 42):
            grid = FakeGrid()
            wrong = {'tile:%d' % i: i + offset for i in range(90)}
            cache = {'panel_ref': PANEL, 'updated': time.time(), 'anchors': wrong,
                     'grid': {'xs': [0, 70, 140, 210, 280], 'shift': 0}}
            for target in (10, 40, 70):
                job, clicks, error = send(grid, target, workspace(), cache=cache)
                self.assert_exact_or_refused(job, clicks, error, target)

    def test_settled_page_uses_strong_anchor_quorum_without_rewriting_conflicts(self):
        total, first, shift, cols = 91, 46, 4, 5
        xs = [0, 70, 140, 210, 280]

        def page(start):
            out = []
            first_row = (start + shift) // cols
            for i in range(30):
                index = start + i
                cell = index + shift
                out.append({'native_ref': f'tile:{index}', 'bounds': {
                    'x': xs[cell % cols], 'y': 100 + (cell // cols - first_row) * 70,
                    'width': 60, 'height': 60}})
            return out

        old = page(41)
        old_first = 41
        grid = selector.favorite_grid(old, old_first)
        self.assertEqual(grid, {'xs': xs, 'shift': shift})
        known = {node['native_ref']: old_first + i for i, node in enumerate(old)}
        current = page(first)
        # Twenty five refs support the right offset. Three recycled refs retain
        # conflicting old indexes; two visible items have no prior ref mapping.
        for i in (6, 12, 18):
            known[current[i]['native_ref']] = first + i + 7
        self.assertEqual(selector.favorite_page_offset(current, known, total, grid), first)
        stale = current[6]['native_ref']
        self.assertNotEqual(known[stale], first+6)
        refs=[node['native_ref'] for node in current]
        for ref in set(value for value in refs if value):
            positions=[i for i,value in enumerate(refs) if value==ref]
            if len(positions)==1:known[ref]=first+positions[0]
            else:known.pop(ref,None)
        self.assertEqual(known[stale], first+6)

        # If too many anchors disagree, geometry alone must not turn a plausible
        # page into a guessed absolute index.
        for i in (2, 3, 4, 5, 7, 8, 9, 10, 11, 12):
            known[current[i]['native_ref']] = first + i + 7
        self.assertIsNone(selector.favorite_page_offset(current, known, total, grid))


if __name__ == '__main__':
    unittest.main()
