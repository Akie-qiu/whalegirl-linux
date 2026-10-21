"""Regression checks for list mutation during an accessibility snapshot."""
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    'a11y_safe', Path(__file__).resolve().parents[1] / 'deploy' / 'a11y_safe.py')
safe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safe)


class StableReferencesTests(unittest.TestCase):
    def test_removed_child_is_skipped_and_next_stable_reference_survives(self):
        root = safe.DESKTOP
        removed, kept = ('test', '/removed'), ('test', '/kept')

        class Bus:
            def role(self, ref):
                if ref == removed:
                    raise RuntimeError('Object disappeared after GetChildren')
                return 'desktop-frame' if ref == root else 'list-item'

            def properties(self, ref):
                return {'Name': 'main' if ref == root else 'kept'}

            def bounds(self, ref):
                return {'x': 0, 'y': 0, 'width': 100, 'height': 30}

            def states(self, ref, role):
                return ['SELECTED'] if ref == kept else []

            def children(self, ref):
                return [removed, kept] if ref == root else []

        tree, stats = safe.dump_tree(Bus())
        self.assertEqual([n['name'] for n in tree['children']], ['kept'])
        self.assertEqual(tree['children'][0]['states'], ['SELECTED'])
        self.assertEqual(stats['stale_nodes_skipped'], 1)
        self.assertEqual(stats['child_index_requests'], 0)

    def test_child_lookup_uses_one_bulk_request_and_filters_null_reference(self):
        calls = []
        bus = object.__new__(safe.A11yBus)

        def call(ref, interface, method, *args):
            calls.append(method)
            return [('test', '/one'), ('test', '/org/a11y/atspi/null'), ('test', '/two')]

        bus.call = call
        self.assertEqual(bus.children(('test', '/list')), [('test', '/one'), ('test', '/two')])
        self.assertEqual(calls, ['GetChildren'])


if __name__ == '__main__':
    unittest.main()
