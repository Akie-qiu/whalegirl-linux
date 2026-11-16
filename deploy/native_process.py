"""Retry only failed process creation; never repeat a submitted UI action."""
import errno
import json
from pathlib import Path
import subprocess
import time


def restore_after_escape(argv):
    if (not isinstance(argv, (list, tuple)) or not argv or Path(str(argv[0])).name != 'xdotool'
            or 'key' not in argv or 'Escape' not in argv):
        return
    version = Path('/data/runtime-version.json')
    if not version.is_file() or json.loads(version.read_text()).get('wechat_version') != '4.1.13.26':
        return
    # The old helpers dismiss menus with Escape. New WeChat also hides its
    # main window when the menu has already closed. Preserve the old behavior
    # after that one key; never repeat the key or the preceding send action.
    from native_voice_compat import restore_main
    import chat_select_linux as ui
    before = ui.SKIP_MESSAGES
    try:
        ui.SKIP_MESSAGES = True
        restore_main(ui)
    finally:
        ui.SKIP_MESSAGES = before


def run(*args, **kwargs):
    for delay in (.08, .2, None):
        try:
            result = subprocess.run(*args, **kwargs)
            if result.returncode == 0:
                try:
                    restore_after_escape(args[0] if args else None)
                except Exception:
                    # An optional focus repair cannot downgrade a completed
                    # send or cause the submitted subprocess to run again.
                    pass
            return result
        except OSError as exc:
            if exc.errno != errno.EAGAIN or delay is None:
                raise
            time.sleep(delay)
