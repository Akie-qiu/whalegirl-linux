"""First receipt survives waiting, metadata enrichment and schema migration."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from whalegirl_linux.store import Store


ROOT = Path(__file__).resolve().parents[1]


class ReceiveTimingTests(unittest.TestCase):
    def setUp(self):
        runs = ROOT / 'test-runs'
        runs.mkdir(exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='receive-timing-', dir=runs))
        self.store = Store(self.folder / 'state.sqlite3')

    def tearDown(self):
        self.store.close()

    def event(self, mid=1, **changes):
        return {'key': f'account/group@chatroom/{mid}', 'account': 'account',
                'chat': 'group@chatroom', 'sender': 'tester', 'sender_id': 'tester',
                'content': 'hello', 'timestamp': 90.0, 'local_id': mid,
                'trigger': 'direct', **changes}

    def test_first_insert_records_receipt_once(self):
        event = self.event(received_at=1.0)
        with patch('whalegirl_linux.store.time.time', return_value=100.0):
            self.assertTrue(self.store.insert(event))
        row = self.store.get(event['key'])
        self.assertEqual((row['received_at'], row['updated']), (100.0, 100.0))
        with patch('whalegirl_linux.store.time.time', return_value=110.0):
            self.assertFalse(self.store.insert(event))
        self.assertEqual(self.store.get(event['key'])['received_at'], 100.0)

    def test_state_updates_and_restart_preserve_first_receipt(self):
        event = self.event()
        with patch('whalegirl_linux.store.time.time', return_value=100.0):
            self.store.insert(event)
        for now, state in ((105.0, 'waiting'), (115.0, 'queued'), (116.0, 'sent')):
            with patch('whalegirl_linux.store.time.time', return_value=now):
                self.store.update(event['key'], state)
            row = self.store.get(event['key'])
            self.assertEqual((row['received_at'], row['updated']), (100.0, now))
        self.store.close()
        self.store = Store(self.folder / 'state.sqlite3')
        self.assertEqual(self.store.get(event['key'])['received_at'], 100.0)

    def test_enriched_ignored_message_keeps_receipt_when_requeued(self):
        event = self.event(sender_id='', trigger='natural')
        with patch('whalegirl_linux.store.time.time', return_value=100.0):
            self.store.insert(event, 'ignored')
        with patch('whalegirl_linux.store.time.time', return_value=115.0):
            self.assertTrue(self.store.insert(self.event()))
        row = self.store.get(event['key'])
        self.assertEqual(row['state'], 'queued')
        self.assertEqual((row['received_at'], row['updated']), (100.0, 115.0))

    def test_old_schema_does_not_guess_historical_receipt(self):
        old_path = self.folder / 'legacy.sqlite3'
        with sqlite3.connect(old_path) as db:
            db.execute('''CREATE TABLE inbox(
                key TEXT PRIMARY KEY, account TEXT NOT NULL, chat TEXT NOT NULL,
                sender TEXT NOT NULL, content TEXT NOT NULL, timestamp REAL NOT NULL,
                state TEXT NOT NULL, reply TEXT NOT NULL DEFAULT '',
                receipt TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                updated REAL NOT NULL)''')
            db.execute('''INSERT INTO inbox(key,account,chat,sender,content,timestamp,state,updated)
                VALUES('account/group@chatroom/local:1','account','group@chatroom',
                'tester','historical',90,'sent',99)''')
        old = Store(old_path)
        try:
            row = old.get('account/group@chatroom/local:1')
            self.assertEqual((row['received_at'], row['updated']), (0.0, 99.0))
            with patch('whalegirl_linux.store.time.time', return_value=150.0):
                old.insert(self.event(2))
            self.assertEqual(old.get(self.event(2)['key'])['received_at'], 150.0)
            column = next(r for r in old.db.execute('PRAGMA table_info(inbox)')
                          if r['name'] == 'received_at')
            self.assertEqual((column['type'], column['notnull'], column['dflt_value']),
                             ('REAL', 1, '0'))
        finally:
            old.close()
        old = Store(old_path)
        try:
            self.assertEqual(old.get('account/group@chatroom/local:1')['received_at'], 0.0)
        finally:
            old.close()


if __name__ == '__main__':
    unittest.main()
