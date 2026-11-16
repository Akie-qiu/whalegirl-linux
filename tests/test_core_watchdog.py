import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from deploy.core_watchdog import (
    FAILURE_THRESHOLD,
    RESTART_COOLDOWN,
    health_verdict,
    maintain_native_memory,
    protect_wechat_oom,
    should_reap_process,
    transition,
)


class CoreWatchdogTests(unittest.TestCase):
    def test_pinned_client_health_and_version_alert_are_separate(self):
        current = {'status': 'ok', 'wechat_version': '4.1.13.26', 'failed_background_tasks': 0}
        good = health_verdict(current, '4.1.13.26')
        self.assertTrue(good['healthy'])
        self.assertTrue(good['version_matches'])
        mismatch = health_verdict(current, '4.1.1.8')
        self.assertTrue(mismatch['healthy'])
        self.assertFalse(mismatch['version_matches'])
        state, action = transition({'consecutive_failures': 815, 'restart_attempts': 168}, 1000, mismatch['healthy'])
        self.assertEqual(action, 'recovered')
        self.assertEqual(state['restart_attempts'], 168)

    def test_failed_background_task_or_down_core_remains_unhealthy(self):
        self.assertFalse(health_verdict({'status': 'ok', 'failed_background_tasks': 1})['healthy'])
        self.assertFalse(health_verdict({'status': 'degraded'})['healthy'])

    def test_restart_requires_three_consecutive_health_failures(self):
        state, action = transition({}, 1000, False)
        self.assertEqual(action, 'waiting')
        state, action = transition(state, 1060, False)
        self.assertEqual(action, 'waiting')
        state, action = transition(state, 1120, False)
        self.assertEqual(action, 'restart')
        self.assertEqual(state['restart_attempts'], 1)
        self.assertEqual(state['consecutive_failures'], FAILURE_THRESHOLD)

    def test_restart_is_throttled_to_one_attempt_per_cooldown(self):
        state, action = transition({}, 1000, False)
        state, action = transition(state, 1060, False)
        state, action = transition(state, 1120, False)
        self.assertEqual(action, 'restart')
        state, action = transition(state, 1180, False)
        self.assertEqual(action, 'cooldown')
        for probe_at in range(1240, 1120 + RESTART_COOLDOWN + 1, 60):
            state, action = transition(state, probe_at, False)
        self.assertEqual(probe_at, 1120 + RESTART_COOLDOWN)
        self.assertEqual(action, 'restart')

    def test_healthy_probe_resets_episode_but_respects_restart_cooldown(self):
        state = {'consecutive_failures': 3, 'last_restart_at': 1000}
        state, action = transition(state, 1060, True)
        self.assertEqual(action, 'recovered')
        state, action = transition(state, 1120, False)
        state, action = transition(state, 1180, False)
        state, action = transition(state, 1240, False)
        self.assertEqual(action, 'cooldown')
        for probe_at in range(1300, 1000 + RESTART_COOLDOWN + 1, 60):
            state, action = transition(state, probe_at, False)
        self.assertEqual(probe_at, 1000 + RESTART_COOLDOWN)
        self.assertEqual(action, 'restart')

    def test_healthy_steady_state_does_not_restart(self):
        state, action = transition({}, 1000, True)
        self.assertEqual(action, 'healthy')
        self.assertEqual(state['consecutive_failures'], 0)
        self.assertEqual(state['last_result'], 'healthy')

    def test_old_failures_do_not_count_after_a_long_sampling_gap(self):
        state = {'consecutive_failures': 2, 'last_probe_at': 1000}
        state, action = transition(state, 1000 + 181, False)
        self.assertEqual(action, 'waiting')
        self.assertEqual(state['consecutive_failures'], 1)

    def test_watchdog_never_auto_kills_wechat_or_helpers(self):
        for cmd in (
            'python3 /opt/tools/a11y-dump --format json',
            'xclip -selection clipboard -t image/png -i /tmp/a.png',
            '/opt/whalegirl-linux/venv/bin/python -m unittest discover',
            '/opt/wechat/RadiumWMPF/runtime/WeChatAppEx --type=renderer',
            '/opt/wechat/crashpad_handler --no-rate-limit',
            '/usr/bin/wechat',
            '/opt/whalegirl-linux/venv/bin/python -m whalegirl_linux',
        ):
            self.assertFalse(should_reap_process(3600, cmd), cmd)

    def test_protect_wechat_oom_and_unbounded_cgroup_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc = root / 'proc'
            cg = root / 'cg'
            proc.mkdir()
            cg.mkdir()
            (cg / 'memory.high').write_text('123456789\n')

            # Fake WeChat main PID + helper PIDs inside container cgroup.
            cid = '58760f6c647d'
            for pid, comm, cmdline, adj in (
                ('101', 'wechat', b'/usr/bin/wechat\0', '0'),
                ('102', 'agent-server', b'/opt/agent-server/agent-server\0', '0'),
                ('103', 'crashpad_handle', b'/opt/wechat/crashpad_handler\0--annotation=ext_info={"app_path":"/opt/wechat/wechat"}\0', '0'),
            ):
                p = proc / pid
                p.mkdir()
                (p / 'cgroup').write_text(f'0::/system.slice/docker-{cid}.scope\n')
                (p / 'comm').write_text(comm + '\n')
                (p / 'cmdline').write_bytes(cmdline)
                (p / 'oom_score_adj').write_text(adj + '\n')

            protected = protect_wechat_oom(cid, proc=proc)
            self.assertEqual((proc / '101' / 'oom_score_adj').read_text().strip(), '-900')
            self.assertEqual((proc / '102' / 'oom_score_adj').read_text().strip(), '-600')
            self.assertEqual((proc / '103' / 'oom_score_adj').read_text().strip(), '0')
            self.assertEqual(len(protected), 2)

            fake_sample = {
                'at': 1.0,
                'container_id': cid,
                'cgroup': str(cg),
                'memory_current': 300_000_000,
                'memory_limit': None,
                'working_bytes': 250_000_000,
                'ratio': 0.0,
                'pressure': False,
            }
            with (
                patch('deploy.core_watchdog.native_resources', return_value=dict(fake_sample)),
                patch('deploy.core_watchdog.publish_native_resources') as pub,
            ):
                result = maintain_native_memory({}, 100.0)
            self.assertEqual((cg / 'memory.high').read_text().strip(), 'max')
            self.assertEqual(result['high_bytes'], 'max')
            pub.assert_called_once()


if __name__ == '__main__':
    unittest.main()
