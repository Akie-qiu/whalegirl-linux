"""A moving Qt page is progress even when its accessible tile refs persist."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
with patch.object(sys, 'path', [str(ROOT / 'deploy'), *sys.path]):
    spec = importlib.util.spec_from_file_location('sticker_scroll_selector', ROOT / 'deploy' / 'chat_select_linux.py')
    selector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selector)


class StickerScrollProgressTests(unittest.TestCase):
    def run_picker(self, *, stuck=False, cold=False):
        runs = ROOT / 'test-runs'
        runs.mkdir(exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix='sticker-scroll-', dir=runs))
        path = folder / 'job.json'
        order = [format(i + 1, '032x') for i in range(10)]
        panel_ref = ':1.2|/org/a11y/atspi/accessible/77'
        refs = [f':1.2|/org/a11y/atspi/accessible/{80+i}' for i in range(5)]
        (folder / 'sticker-panel-anchors.cache').write_text(json.dumps({
            'panel_ref': panel_ref, 'order': order, 'updated': time.time(),
            'anchors': {} if cold else {ref: i for i, ref in enumerate(refs[:3])},
        }))
        state = {'step': 0, 'boundary': False}
        commands = []

        def page():
            step = (state['step'] if cold else
                    3 if state['boundary'] else 0 if stuck else state['step'])
            page_refs = refs[2:5] if cold and step >= 4 else refs[:3]
            tiles = [{'role': 'label', 'name': 'Custom Stickers', 'native_ref': ref,
                      'bounds': {'x': 0, 'y': (40 if cold and step >= 4 else 100 - step * 20) + i * 60,
                                 'width': 60, 'height': 60}}
                     for i, ref in enumerate(page_refs)]
            return {'role': 'frame', 'children': [
                {'role': 'page-tab', 'name': 'Custom Stickers'},
                {'role': 'list', 'native_ref': panel_ref,
                 'bounds': {'x': 0, 'y': 100, 'width': 100, 'height': 100}, 'children': tiles},
            ]}

        def command(argv, **options):
            commands.append(argv)
            if '--repeat' in argv:
                count = int(argv[argv.index('--repeat') + 1])
                if count == 40:
                    state['boundary'] = True
                else:
                    state['step'] += 1

        def tree(**options):
            return page()

        job = {'md5': order[4 if cold else 2], 'chat': 'test', 'created': time.time()}
        with patch.object(selector, 'PROFILE', {'snapshots': 0, 'snapshot_seconds': 0.0}) as profile, \
             patch.object(selector, 'native_read', return_value={'stickers': [{'md5': item} for item in order]}), \
             patch.object(selector, 'selected', return_value=True), \
             patch.object(selector, 'run_process', side_effect=command), \
             patch.object(selector, 'tree', side_effect=tree), \
             patch.object(selector.time, 'sleep'):
            selector.send_native_sticker(job, path, page(), 'test', True)
            counts = dict(profile)
        return job, commands, counts

    def test_identical_refs_with_moving_bounds_keep_locating_from_current_page(self):
        job, commands, counts = self.run_picker()
        self.assertEqual(job['state'], 'submitted')
        self.assertEqual(job['favorite_cache'], 'anchor_scroll')
        self.assertEqual(counts['anchor_scroll'], 3)
        self.assertEqual(counts.get('boundary_scroll', 0), 0)
        self.assertFalse(any('--repeat' in args and args[args.index('--repeat') + 1] == '40'
                             for args in commands))
        self.assertEqual(len([args for args in commands if args[0] == '/opt/tools/click']), 1)

    def test_truly_unchanged_page_still_falls_back_to_boundary(self):
        job, commands, counts = self.run_picker(stuck=True)
        self.assertEqual(job['state'], 'submitted')
        self.assertEqual(counts['anchor_scroll'], 1)
        self.assertEqual(counts['boundary_scroll'], 1)
        self.assertTrue(any('--repeat' in args and args[args.index('--repeat') + 1] == '40'
                            for args in commands))

    def test_boundary_alignment_does_not_count_moving_objects_as_stagnant(self):
        job, _, counts = self.run_picker(cold=True)
        self.assertEqual(job['state'], 'submitted')
        self.assertEqual(counts['alignment_scroll'], 4)
        self.assertEqual(counts['boundary_scroll'], 1)
        self.assertEqual(len(job['favorite_scroll']), 4)


if __name__ == '__main__':
    unittest.main()
