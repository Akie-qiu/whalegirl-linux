"""The native sticker anchor cache needs its owning panel's stable identity."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('sticker_panel_a11y', ROOT / 'deploy' / 'a11y_safe.py')
A11Y = importlib.util.module_from_spec(SPEC)
if sys.platform == 'win32':
    # These pure traversal tests do not invoke the Linux lock entry point.
    with patch.dict(sys.modules, {'fcntl': types.ModuleType('fcntl')}):
        SPEC.loader.exec_module(A11Y)
else:
    SPEC.loader.exec_module(A11Y)


class Bus:
    panel = ('test.app', '/stickers')
    tile = ('test.app', '/stickers/tile-1')
    chats = ('test.app', '/chats')
    row = ('test.app', '/chats/row-1')

    def __init__(self, y=100):
        self.y = y

    def role(self, ref):
        return {A11Y.DESKTOP: 'desktop-frame', self.panel: 'list', self.tile: 'label',
                self.chats: 'list', self.row: 'list-item'}[ref]

    def properties(self, ref):
        return {'Name': {self.tile: 'Custom Stickers', self.chats: 'Chats',
                         self.row: 'example chat'}.get(ref, '')}

    def bounds(self, ref):
        return {'x': 0, 'y': self.y, 'width': 100, 'height': 60}

    def states(self, ref, role):
        return []

    def children(self, ref):
        return {A11Y.DESKTOP: [self.chats, self.panel], self.panel: [self.tile],
                self.chats: [self.row]}.get(ref, [])


class StickerPanelIdentityTests(unittest.TestCase):
    def snapshot(self, y=100):
        tree, _ = A11Y.dump_tree(Bus(y), skip_messages=True)
        return tree['children']

    def test_sticker_panel_reference_is_stable_when_bounds_change(self):
        _, panel = self.snapshot()
        _, moved_panel = self.snapshot(180)
        self.assertEqual(panel['native_ref'], 'test.app|/stickers')
        self.assertEqual(panel['native_ref'], moved_panel['native_ref'])
        self.assertNotEqual(panel['bounds'], moved_panel['bounds'])

    def test_ordinary_list_keeps_existing_shape(self):
        chats, _ = self.snapshot()
        self.assertNotIn('native_ref', chats)
        self.assertEqual(chats['role'], 'list')
        self.assertEqual(chats['name'], 'Chats')
        self.assertEqual(chats['children'][0]['name'], 'example chat')

    def test_sticker_tile_retains_its_own_reference(self):
        _, panel = self.snapshot()
        tile = panel['children'][0]
        self.assertEqual(tile['native_ref'], 'test.app|/stickers/tile-1')
        self.assertNotEqual(tile['native_ref'], panel['native_ref'])


if __name__ == '__main__':
    unittest.main()
