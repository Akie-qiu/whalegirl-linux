import unittest

from whalegirl_linux.runtime import exception_location


class RuntimeErrorDiagnosticTests(unittest.TestCase):
    def test_location_identifies_source_without_exposing_exception_message(self):
        try:
            raise AttributeError('private message text')
        except AttributeError as exc:
            value = exception_location(exc)

        self.assertIn('test_runtime_error_diagnostics.py:', value)
        self.assertNotIn('private message text', value)

    def test_missing_traceback_is_safe(self):
        self.assertEqual(exception_location(AttributeError('no traceback')), 'unknown')


if __name__ == '__main__':
    unittest.main()
