"""Mid-list favorites must be reachable on WeChat's real, animated grid.

A live no-send probe (2026-11-09) showed that one wheel notch moves the
favorite grid by ~37 px while a row is 88 px tall, and that Qt animates each
notch for ~0.15-0.3 s. Reads taken during the animation show tiles moving by
different amounts. The selector used to read 0.06 s after each notch, refused
those pages, lost its anchors and failed every favorite in the middle of the
list (indices ~25-68 of 91; ~40 % of sticker sends on 11-08).
"""
import importlib.util
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('grid_safety_base', HERE / 'test_sticker_grid_safety.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


class AnimatedGrid(base.FakeGrid):
    """Sub-row wheel notches plus non-rigid reads while Qt is still animating."""

    def __init__(self, notch=37, animated_reads=2, **options):
        super().__init__(**options)
        self.pitch, self.size = 88, 80
        self.area = {'x': 0, 'y': 150, 'width': self.cols * self.pitch, 'height': 412}
        rows = -(-(self.total + self.shift) // self.cols)
        self.max_scroll = max(0, rows * self.pitch - self.area['height'])
        self.notch = notch
        self.animated_reads = animated_reads
        self.animating = []

    def wheel(self, direction, repeat):
        start = self.scroll
        if repeat >= 40:
            target = 0 if direction == '4' else self.max_scroll
        else:
            step = -self.notch if direction == '4' else self.notch
            target = start + step * repeat
        target = min(max(target, 0), self.max_scroll)
        # A wheel against the end of the list does not start an animation.
        self.animating = [] if target == start else [
            start + (target - start) * (k + 1) // (self.animated_reads + 1)
            for k in range(self.animated_reads)]
        self.scroll = target

    def tiles(self):
        self.reads += 1
        moving = bool(self.animating)
        scroll = self.animating.pop(0) if moving else self.scroll
        first_row = max(0, scroll // self.pitch - 1)
        last_row = (scroll + self.area['height']) // self.pitch
        out = []
        for item in range(self.total):
            row, col = divmod(item + self.shift, self.cols)
            if not first_row <= row <= last_row:
                continue
            # Mid-animation rows are repainted at different, changing offsets
            # (live reads showed per-row moves such as 22/25/27 px).
            wobble = ((row + self.reads) % 3) * 3 if moving else 0
            out.append({'role': 'label', 'name': 'Custom Stickers', 'native_ref': 'tile:%d' % item,
                        'bounds': {'x': col * self.pitch + 13, 'y': self.area['y'] + row * self.pitch - scroll + 5 + wobble,
                                   'width': self.size, 'height': self.size}})
        out.sort(key=lambda n: (n['bounds']['y'], n['bounds']['x']))
        return out

    def command(self, argv, **options):
        if argv[0] == '/opt/tools/click':
            x, y = int(argv[1]), int(argv[2])
            row = (y - self.area['y'] - 5 + self.scroll) // self.pitch
            col = (x - 13) // self.pitch
            self.clicked.append(row * self.cols + col - self.shift)
            return None
        return super().command(argv, **options)


class AnimatedGridTests(unittest.TestCase):
    def test_every_favorite_is_reached_on_an_animated_sub_row_grid(self):
        failures = []
        for target in range(91):
            grid = AnimatedGrid(total=91)
            job, clicks, error = base.send(grid, target, base.workspace())
            if error is not None or clicks != [target]:
                failures.append((target, error, clicks))
            if error is None:
                self.assertEqual(clicks, [target], 'clicked a different favorite')
        self.assertEqual(failures, [])

    def test_warm_sequence_on_animated_grid_stays_exact(self):
        grid = AnimatedGrid(total=91)
        folder = base.workspace()
        sent = 0
        for target in (42, 49, 25, 68, 3, 87, 56, 41, 48, 35, 61, 29):
            job, clicks, error = base.send(grid, target, folder)
            if error is None:
                self.assertEqual(clicks, [target])
                sent += 1
            else:
                self.assertEqual(clicks, [], error)
        self.assertEqual(sent, 12)


if __name__ == '__main__':
    unittest.main()
