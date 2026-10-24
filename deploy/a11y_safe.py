#!/usr/bin/env python3
"""Read stable AT-SPI object references without count/index traversal.

The pinned client's accessible child(index) crashes on an index made stale by
an updating list. GetChildren returns all references in one client event; later
reads of a removed reference fail normally instead of resolving an old index.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


ACCESSIBLE = 'org.a11y.atspi.Accessible'
COMPONENT = 'org.a11y.atspi.Component'
PROPERTIES = 'org.freedesktop.DBus.Properties'
DESKTOP = ('org.a11y.atspi.Registry', '/org/a11y/atspi/accessible/root')
USEFUL_STATES = {'FOCUSED', 'SELECTED', 'CHECKED', 'PRESSED', 'EXPANDED',
                 'COLLAPSED', 'ACTIVE', 'ARMED', 'BUSY', 'EDITABLE'}
INTERACTIVE = {'push-button', 'toggle-button', 'check-box', 'radio-button',
               'text', 'combo-box', 'menu-item', 'spin-button'}


class A11yBus:
    def __init__(self, *, deadline=None):
        self.deadline = deadline
        import gi
        gi.require_version('Atspi', '2.0')
        from gi.repository import Atspi, Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.state_names = {int(k): v.value_name.upper().replace('ATSPI_STATE_', '')
                            for k, v in Atspi.StateType.__enum_values__.items()}
        # The gateway runs as root and the desktop session belongs to WeChat.
        # Use the display's advertised accessibility bus, as libatspi does,
        # rather than authenticating to the desktop user's session bus.
        advertised = subprocess.run(['xprop', '-root', 'AT_SPI_BUS'],
            capture_output=True, text=True, timeout=3)
        match = re.search(r'= "(unix:[^"]+)"', advertised.stdout)
        if match:
            address = match.group(1)
        else:
            session = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            address = session.call_sync('org.a11y.Bus', '/org/a11y/bus', 'org.a11y.Bus',
                                        'GetAddress', None, None, Gio.DBusCallFlags.NONE,
                                        1800, None).unpack()[0]
        self.connection = Gio.DBusConnection.new_for_address_sync(
            address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
            Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        self.pids = {}

    def call(self, ref, interface, method, signature=None, values=()):
        remaining = self.deadline-time.monotonic() if self.deadline is not None else 1.5
        if remaining <= 0:
            raise TimeoutError('Accessibility snapshot deadline reached')
        args = self.GLib.Variant(signature, values) if signature else None
        reply = self.connection.call_sync(ref[0], ref[1], interface, method, args, None,
                                          self.Gio.DBusCallFlags.NONE, max(1, min(1500, int(remaining*1000))), None)
        result = reply.unpack()
        return result[0] if len(result) == 1 else result

    def children(self, ref):
        # Never issue GetChildCount followed by GetChildAtIndex: the second
        # request may run against a newer native list than the first.
        return [tuple(child) for child in self.call(ref, ACCESSIBLE, 'GetChildren')
                if child[0] and child[1] != '/org/a11y/atspi/null']

    def properties(self, ref):
        if ref == DESKTOP:
            # The registry desktop implements GetChildren but no accessible
            # properties; libatspi supplies its synthetic desktop name.
            return {'Name': 'main'}
        # This Qt build supports individual properties, but not GetAll.
        return {'Name': self.call(ref, PROPERTIES, 'Get', '(ss)', (ACCESSIBLE, 'Name'))}

    def role(self, ref):
        return self.call(ref, ACCESSIBLE, 'GetRoleName').replace(' ', '-')

    def bounds(self, ref):
        values = self.call(ref, COMPONENT, 'GetExtents', '(u)', (0,))
        x, y, width, height = values
        return dict(x=x, y=y, width=width, height=height) if width > 0 and height > 0 else None

    def states(self, ref, role):
        masks = self.call(ref, ACCESSIBLE, 'GetState')
        names = {self.state_names.get(word * 32 + bit, '')
                 for word, mask in enumerate(masks) for bit in range(32) if mask & (1 << bit)}
        useful = sorted(names & USEFUL_STATES)
        if role in INTERACTIVE and 'ENABLED' not in names:
            useful.append('DISABLED')
        return useful

    def window(self, ref):
        if ref[0] not in self.pids:
            self.pids[ref[0]] = self.call(('org.freedesktop.DBus', '/org/freedesktop/DBus'),
                'org.freedesktop.DBus', 'GetConnectionUnixProcessID', '(s)', (ref[0],))
        return {'pid': self.pids[ref[0]]}


def dump_tree(bus, max_depth=30, *, skip_messages=False, deadline=None):
    stats = {'nodes': 0, 'bulk_requests': 0, 'stale_nodes_skipped': 0,
             'child_index_requests': 0}
    seen = set()

    def walk(ref, depth):
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('Accessibility snapshot deadline reached')
        if depth > max_depth or ref in seen:
            return None
        seen.add(ref)
        try:
            role = bus.role(ref)
            name = bus.properties(ref).get('Name', '') or ''
            if role in ('push-button', 'label') and name == 'Enter Weixin':
                name = 'Log In'
            try:
                bounds = bus.bounds(ref)
            except Exception:
                bounds = None
            if depth > 1 and bounds is None or role == 'label' and not name:
                return None
            result = {'role': role, 'name': name}
            if role == 'label' and name == 'Custom Stickers':
                # Stable object identity lets the selector align overlapping
                # virtual pages without guessing how far a wheel event moved.
                result['native_ref'] = ref[0] + '|' + ref[1]
            if bounds:
                result['bounds'] = bounds
            try:
                states = bus.states(ref, role)
                if states:
                    result['states'] = states
            except Exception:
                pass
            if role == 'frame':
                try:
                    result['window'] = bus.window(ref)
                except Exception:
                    pass
            stats['nodes'] += 1
            # Sending only needs the destination, composer and sticker controls.
            # Message/quote/image reads continue to request the complete tree.
            if skip_messages and role == 'list' and name == 'Messages':
                return result
            # Freeze this parent's object references in one native call. An
            # item that vanishes later is skipped; never fall back to an index.
            stats['bulk_requests'] += 1
            refs = bus.children(ref) if depth < max_depth else []
            children = [child for child in (walk(r, depth + 1) for r in refs) if child]
            if children:
                result['children'] = children
            return result
        except TimeoutError:
            raise
        except Exception:
            stats['stale_nodes_skipped'] += 1
            return None

    return walk(DESKTOP, 0), stats


def tree_aria(node, depth=0):
    if not node:
        return ''
    label = node.get('name', '')
    line = '  ' * depth + '- ' + node['role'] + (' ' + json.dumps(label, ensure_ascii=False) if label else '')
    if node.get('states'):
        line += ' [' + ','.join(node['states']) + ']'
    if node.get('bounds'):
        b = node['bounds']
        line += f" @({b['x']},{b['y']} {b['width']}x{b['height']})"
    return '\n'.join([line, *(tree_aria(child, depth + 1) for child in node.get('children', []))])


def publish_stats(directory, stats):
    # Per-UID files avoid root and the WeChat desktop user replacing each
    # other's owned files. The telemetry contains counts, never chat content.
    target = directory / ('latest-' + str(os.getuid()) + '.json')
    pending = directory / ('pending-' + str(os.getpid()) + '.json')
    pending.write_text(json.dumps(stats), encoding='utf-8')
    pending.chmod(0o600)
    os.replace(pending, target)


def acquire_read_lock(lock, *, timeout=1.2):
    # The gateway doesn't reap timed-out child commands. Unbounded flock would
    # leave cancelled readers queued for tens of seconds and starve health scans.
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError('Accessibility reader busy') from None
            time.sleep(.025)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--format', choices=['json', 'aria'], default='json')
    parser.add_argument('--max-depth', type=int, default=30)
    parser.add_argument('--skip-messages', action='store_true')
    args = parser.parse_args()
    directory = Path('/data/native-tools/a11y-state')
    started = time.monotonic()
    with (directory / 'read.lock').open('r+') as lock:
        locked = started
        try:
            acquire_read_lock(lock)
            locked = time.monotonic()
            deadline = started + 6.5
            tree, stats = dump_tree(A11yBus(deadline=deadline), args.max_depth,
                                    skip_messages=args.skip_messages, deadline=deadline)
            stats.update(at=time.time(), elapsed_seconds=round(time.monotonic() - started, 4),
                         lock_wait_seconds=round(locked - started, 4), ok=bool(tree))
            publish_stats(directory, stats)
            if not tree:
                raise RuntimeError('Native accessibility snapshot unavailable')
            print(tree_aria(tree) if args.format == 'aria' else json.dumps(tree, ensure_ascii=False))
        except Exception as exc:
            publish_stats(directory, {'at': time.time(), 'ok': False, 'error': type(exc).__name__,
                'elapsed_seconds': round(time.monotonic()-started,4),
                'lock_wait_seconds': round((locked if locked>started else time.monotonic())-started,4)})
            raise
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Do not log D-Bus exception text, which can contain node labels.
        print(json.dumps({'error': type(exc).__name__}))
        sys.exit(1)
