"""Sticker scroll snapshots stay inside their validated AT-SPI panel."""
import time
import unittest

from test_sticker_panel_identity import A11Y, Bus


class TrackedBus(Bus):
    def __init__(self, *, stale=False, nonpanel=False, empty=False):
        super().__init__()
        self.reads = []
        self.stale, self.nonpanel, self.empty = stale, nonpanel, empty

    def role(self, ref):
        self.reads.append(ref)
        if self.stale and ref == self.panel:
            raise RuntimeError('Accessible object was removed')
        return 'frame' if self.nonpanel and ref == self.panel else super().role(ref)

    def children(self, ref):
        return [] if self.empty and ref == self.panel else super().children(ref)


class StickerSubtreeTests(unittest.TestCase):
    def test_subtree_does_not_traverse_desktop_or_chat_lists(self):
        bus = TrackedBus()
        tree, stats = A11Y.dump_tree(bus, root_ref=bus.panel)
        self.assertEqual(bus.reads, [bus.panel, bus.tile])
        self.assertEqual(tree['native_ref'], 'test.app|/stickers')
        self.assertEqual(tree['children'][0]['name'], 'Custom Stickers')
        self.assertEqual(stats['child_index_requests'], 0)
        self.assertEqual(stats['nodes'], 2)

    def test_default_root_still_reads_normal_desktop(self):
        bus = TrackedBus()
        tree, _ = A11Y.dump_tree(bus)
        self.assertEqual(bus.reads[0], A11Y.DESKTOP)
        self.assertIn(bus.chats, bus.reads)
        self.assertEqual([node['role'] for node in tree['children']], ['list', 'list'])

    def test_stale_or_nonpanel_ref_is_rejected(self):
        for bus in (TrackedBus(stale=True), TrackedBus(nonpanel=True), TrackedBus(empty=True)):
            with self.subTest(stale=bus.stale, nonpanel=bus.nonpanel, empty=bus.empty):
                with self.assertRaises(ValueError):
                    A11Y.dump_tree(bus, root_ref=bus.panel)
        bus = TrackedBus()
        with self.assertRaises(ValueError):
            A11Y.dump_tree(bus, root_ref=bus.chats)

    def test_reference_parser_accepts_native_dbus_accessible_refs(self):
        for name in (':1.42', 'org.a11y.atspi.Registry'):
            path = '/org/a11y/atspi/accessible/2147483756'
            self.assertEqual(A11Y.parse_sticker_panel_ref(name + '|' + path), (name, path))

    def test_reference_parser_rejects_null_and_abnormal_paths(self):
        for value in ('', None, 'test', ':1.2|/org/a11y/atspi/null',
                      ':1.2|/org/a11y/atspi/accessible/null', ':1.2|/other/77',
                      ':1.2|/org/a11y/atspi/accessible/../77',
                      ':1.2|/org/a11y/atspi/accessible//77',
                      ':1.2|/org/a11y/atspi/accessible/77|extra',
                      ':1.2\n|/org/a11y/atspi/accessible/77',
                      ':1|/org/a11y/atspi/accessible/77'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    A11Y.parse_sticker_panel_ref(value)

    def test_subtree_keeps_existing_deadline(self):
        bus = TrackedBus()
        with self.assertRaises(TimeoutError):
            A11Y.dump_tree(bus, root_ref=bus.panel, deadline=time.monotonic() - 1)
        self.assertFalse(bus.reads)


if __name__ == '__main__':
    unittest.main()
