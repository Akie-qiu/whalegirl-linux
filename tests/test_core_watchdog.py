import unittest

from deploy.core_watchdog import FAILURE_THRESHOLD, RESTART_COOLDOWN, transition


class CoreWatchdogTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
