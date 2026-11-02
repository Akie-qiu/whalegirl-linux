"""Connection reuse keeps live reads, bounded locks and error recovery."""
import importlib.util
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_panel_reader_test', ROOT / 'deploy' / 'native_panel_reader.py')
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)


class NativePanelReaderTests(unittest.TestCase):
    def setUp(self):
        runs = ROOT / 'test-runs'
        runs.mkdir(exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='panel-reader-', dir=runs))
        (self.folder / 'read.lock').write_text('')
        self.events = []
        self.connections = []

        def new_bus(**options):
            bus = types.SimpleNamespace(deadline=options['deadline'], connection=Mock())
            self.connections.append(bus)
            return bus

        self.safe = types.SimpleNamespace(
            parse_sticker_panel_ref=Mock(return_value=(':1.2', '/org/a11y/atspi/accessible/77')),
            acquire_read_lock=Mock(side_effect=lambda lock: self.events.append('acquire')),
            A11yBus=Mock(side_effect=new_bus),
            publish_stats=Mock(),
            fcntl=types.SimpleNamespace(LOCK_UN=8,
                flock=Mock(side_effect=lambda lock, mode: self.events.append('unlock'))),
        )
        self.directory = patch.object(reader, 'STATE_DIRECTORY', self.folder)
        self.directory.start()
        self.importer = patch.object(reader.importlib, 'import_module', return_value=self.safe)
        self.importer.start()
        reader._BUS = None

    def tearDown(self):
        reader._discard_bus()
        self.importer.stop()
        self.directory.stop()

    def test_reuses_connection_but_refreshes_deadline_and_reads_every_time(self):
        deadlines = []

        def dump(bus, **options):
            self.events.append('dump')
            self.assertEqual(bus.deadline, options['deadline'])
            self.assertTrue(options['skip_messages'])
            self.assertEqual(options['root_ref'], (':1.2', '/org/a11y/atspi/accessible/77'))
            deadlines.append(options['deadline'])
            return {'role': 'list', 'bounds': {'y': len(deadlines)}}, {'nodes': 2}

        self.safe.dump_tree = Mock(side_effect=dump)
        with patch.object(reader.time, 'monotonic', side_effect=[10, 10.1, 10.2, 20, 20.1, 20.2]):
            first = reader.read('native ref')
            second = reader.read('native ref')
        self.assertEqual(deadlines, [16.5, 26.5])
        self.assertEqual(self.safe.A11yBus.call_count, 1)
        self.assertEqual(self.safe.dump_tree.call_count, 2)
        self.assertNotEqual(first['bounds'], second['bounds'])
        self.assertEqual(self.events, ['acquire', 'dump', 'unlock'] * 2)
        self.assertTrue(all(call.args[1]['panel_read'] for call in self.safe.publish_stats.call_args_list))

    def test_read_error_unlocks_discards_connection_and_next_call_recovers(self):
        original = ValueError('stale reference')
        self.safe.dump_tree = Mock(side_effect=[original, ({'role': 'list'}, {'nodes': 1})])
        with self.assertRaises(ValueError) as caught:
            reader.read('native ref')
        self.assertIs(caught.exception, original)
        self.assertIsNone(reader._BUS)
        self.connections[0].connection.close_sync.assert_called_once_with(None)
        self.assertEqual(self.events, ['acquire', 'unlock'])
        self.assertEqual(reader.read('native ref'), {'role': 'list'})
        self.assertEqual(self.safe.A11yBus.call_count, 2)
        self.assertEqual(self.events, ['acquire', 'unlock'] * 2)

    def test_cleanup_failure_does_not_hide_original_read_error(self):
        original = RuntimeError('read failed')

        def dump(bus, **options):
            bus.connection.close_sync.side_effect = OSError('close failed')
            raise original

        self.safe.dump_tree = Mock(side_effect=dump)
        self.safe.publish_stats.side_effect = OSError('telemetry failed')
        self.safe.fcntl.flock.side_effect = OSError('unlock failed')
        with self.assertRaises(RuntimeError) as caught:
            reader.read('native ref')
        self.assertIs(caught.exception, original)
        self.assertIsNone(reader._BUS)
        self.safe.fcntl.flock.assert_called_once()


if __name__ == '__main__':
    unittest.main()
