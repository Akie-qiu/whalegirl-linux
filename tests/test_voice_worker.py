"""Linux voice-worker checks without controlling or sending to WeChat."""
import importlib.util
import base64
import struct
from pathlib import Path
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'deploy' / 'voice_send_worker.py'
spec = importlib.util.spec_from_file_location('voice_worker_fixture', PATH)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def node(role, name, **values):
    return dict(role=role, name=name, bounds=dict(x=100, y=100, width=20, height=20), **values)


class VoiceWorkerTests(unittest.TestCase):
    @staticmethod
    def silk(packets=244):
        return b'\x02#!SILK_V3' + (struct.pack('<h', 8) + b'abcdefgh') * packets

    def test_tencent_silk_packets_are_validated_without_ffmpeg(self):
        part = {'playbackDurationSeconds': 4.26, 'recordingElapsedSeconds': 7}
        row = {'type': 34, 'isSelf': True, 'serverId': '123',
               'content': '<msg><voicemsg voiceformat="4" voicelength="4880" /></msg>'}
        with patch.object(worker, 'command', side_effect=AssertionError('SILK must not go to FFmpeg')):
            duration = worker.verified_payload(part, row, {'data': base64.b64encode(self.silk()).decode()})
        self.assertAlmostEqual(duration, 4.88)
        self.assertEqual(part['verification'], 'silk_packets_and_native_server_receipt')
        self.assertFalse(part['audioContentVerified'])
        self.assertNotIn('decodedDurationSeconds', part)

    def test_silk_truncated_or_empty_packet_stream_is_rejected(self):
        for value in [b'\x02#!SILK_V3', self.silk()[:-1], b'\x02#!SILK_V3\x10\x00x']:
            with self.subTest(size=len(value)), self.assertRaises(ValueError):
                worker.silk_duration(value)

    def test_silk_must_match_native_duration_and_have_server_ack(self):
        part = {'playbackDurationSeconds': 4.26}
        row = {'type': 34, 'isSelf': True, 'serverId': '0',
               'content': '<msg><voicemsg voiceformat="4" voicelength="4880" /></msg>'}
        media = {'data': base64.b64encode(self.silk()).decode()}
        with self.assertRaises(RuntimeError):
            worker.verified_payload(part, row, media)
        row.update(serverId='123', content='<msg><voicemsg voiceformat="4" voicelength="2000" /></msg>')
        with self.assertRaises(RuntimeError):
            worker.verified_payload(part, row, media)

    def test_group_header_can_include_member_count(self):
        for header in ['Test', 'Test (3)', 'Test（3）']:
            root = dict(children=[node('list-item', 'Test latest', states=['SELECTED']), node('label', header)])
            self.assertTrue(worker.destination_ok(root, 'Test'))

    def test_header_must_not_match_similar_group(self):
        root = dict(children=[node('list-item', 'Test latest', states=['SELECTED']), node('label', 'Test two')])
        self.assertFalse(worker.destination_ok(root, 'Test'))

    def test_duplicate_accessibility_nodes_are_one_physical_control(self):
        first = node('push-button', 'Cancel')
        self.assertEqual(worker.control(dict(children=[first, dict(first)]), 'Cancel'), first)

    def test_distinct_controls_fail_closed(self):
        first = node('push-button', 'Cancel')
        second = node('push-button', 'Cancel')
        second['bounds']['x'] = 200
        with self.assertRaises(RuntimeError):
            worker.control(dict(children=[first, second]), 'Cancel')

    def test_chunk_boundaries_retain_all_samples(self):
        for seconds in [1, 49.9, 50, 50.1, 100.1, 120]:
            total = round(seconds * worker.RATE)
            chunks = worker.bounds(total)
            self.assertEqual(chunks[0][0], 0)
            self.assertEqual(chunks[-1][1], total)
            self.assertEqual(sum(b-a for a, b in chunks), total)
            self.assertTrue(all(b-a <= worker.MAX_SAMPLES for a, b in chunks))

    def test_audio_commands_limit_library_threads(self):
        with patch.object(worker.subprocess, 'run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b'ok'
            self.assertEqual(worker.command('ffmpeg'), b'ok')
        self.assertEqual(run.call_args.kwargs['env']['OPENBLAS_NUM_THREADS'], '1')

    def test_new_pinned_version_is_explicitly_validated(self):
        from whalegirl_linux.settings import Settings
        self.assertEqual(Settings(expected_wechat_version='4.1.13.26').expected_wechat_version, '4.1.13.26')
        with self.assertRaises(ValueError):
            Settings(expected_wechat_version='unverified-version')


if __name__ == '__main__':
    unittest.main()
