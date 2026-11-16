"""Native compatibility fixtures: no GUI calls, real accounts or sends."""
import ast
import importlib.util
from pathlib import Path
import re
from types import SimpleNamespace
import unicodedata
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location('fixture_' + name, ROOT / 'deploy' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compat = load('native_voice_compat')
process = load('native_process')


def walk(node):
    yield node
    for child in node.get('children', []):
        yield from walk(child)


class NativeVoiceCompatibilityTests(unittest.TestCase):
    def test_search_uses_group_section_not_suggestion_or_history(self):
        rows = [dict(role='list-item', name=name, bounds={'height': height}) for name, height in
                [('Test', 34), ('Group Chats', 32), ('Test', 64), ('Chat History', 32), ('Test', 88)]]
        root = dict(children=[dict(role='list', name='', children=rows)])
        self.assertIs(compat.group_search_result(root, 'Test', walk), rows[2])

    def test_duplicate_groups_refuse_selection(self):
        rows = [dict(role='list-item', name=name, bounds={'height': height}) for name, height in
                [('Group Chats', 32), ('Test', 64), ('Test', 64)]]
        with self.assertRaises(ValueError):
            compat.group_search_result(dict(role='list', name='', children=rows), 'Test', walk)

    def test_visible_main_window_does_not_toggle_tray(self):
        ui = SimpleNamespace(tree=lambda: dict(role='frame', name='Weixin', bounds={'width': 868}), walk=walk, run_process=Mock())
        compat.restore_main(ui)
        ui.run_process.assert_not_called()

    def test_selected_row_accepts_newline_and_window_position(self):
        source = ast.parse((ROOT / 'deploy/chat_select_linux.py').read_text(encoding='utf-8'))
        functions = [f for f in source.body if isinstance(f, ast.FunctionDef) and
                     f.name in ('normal', 'walk', 'header_matches', 'selected')]
        namespace = dict(re=re, unicodedata=unicodedata)
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<selection-fixture>', 'exec'), namespace)
        root = dict(children=[dict(role='list', name='Chats', bounds={'x': 100, 'width': 200, 'y': 180},
                    children=[dict(role='list-item', name='Test\nrecent text', states=['SELECTED'])]),
                    dict(role='label', name='Test(3)', bounds={'x': 320, 'y': 140})])
        self.assertTrue(namespace['selected'](root, 'Test', True))
        self.assertFalse(namespace['selected'](root, 'Tes', True))

    def test_post_escape_focus_error_never_repeats_submitted_key(self):
        result = SimpleNamespace(returncode=0)
        with patch.object(process.subprocess, 'run', return_value=result) as run, \
                patch.object(process, 'restore_after_escape', side_effect=RuntimeError('focus unavailable')):
            self.assertIs(process.run(['xdotool', 'key', 'Escape']), result)
        self.assertEqual(run.call_count, 1)

    def test_eagain_only_retries_process_creation(self):
        import errno
        result = SimpleNamespace(returncode=0)
        with patch.object(process.subprocess, 'run', side_effect=[OSError(errno.EAGAIN, 'busy'), result]) as run, \
                patch.object(process, 'restore_after_escape'), patch.object(process.time, 'sleep'):
            self.assertIs(process.run(['xdotool', 'key', 'Escape']), result)
        self.assertEqual(run.call_count, 2)


if __name__ == '__main__':
    unittest.main()
